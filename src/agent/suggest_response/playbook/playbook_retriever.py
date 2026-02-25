"""
PlaybookRetriever: agentic loop that searches and selects playbooks via tools,
then returns formatted system-reminder text for injection into suggest_response.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import asyncpg

from src.agent.common.conversation_settings import get_reasoning_param
from src.agent.core.llm_call import LLM_call
from src.database.postgres.repositories import (
    get_assigned_playbook_ids,
    get_facebook_page_admins_by_user_id,
)
from src.database.postgres.repositories.agent_queries.agent_responses import (
    insert_openai_response_with_agent,
)
from src.agent.suggest_response.playbook.constants import MAX_ITERATIONS, MAX_SEARCHES
from src.agent.suggest_response.playbook.tools.tool_definitions import (
    get_playbook_tool_definitions,
    get_playbook_tool_definitions_select_only,
)
from src.agent.suggest_response.playbook.helpers import (
    build_initial_input_messages,
    format_playbooks_as_system_reminder,
    input_items_for_api,
)
from src.agent.suggest_response.playbook.tool_handler import PlaybookToolHandler
from src.agent.suggest_response.socket.emitter import SuggestResponseSocketEmitter
from src.agent.suggest_response.socket.stream_handler import SuggestResponseStreamHandler
from src.agent.suggest_response.utils.prompt_logger import log_playbook_retriever_input
from src.api.openai_conversations.schemas import MessageResponse
from src.utils.logger import get_logger

logger = get_logger()

STEP = "playbook_retrieval"


@dataclass
class PlaybookRetrievalResult:
    """Result of playbook retrieval: formatted text to inject and messages for persistence."""

    system_reminder: Optional[str]
    accumulated_messages: List[MessageResponse]


class PlaybookRetriever:
    """
    Retrieves relevant playbooks via an agentic loop: LLM can search (max 3 times)
    and then select which playbooks to use. Streams reasoning and tool events.
    """

    def __init__(
        self, socket_emitter: Optional[SuggestResponseSocketEmitter] = None
    ) -> None:
        self.socket_emitter = socket_emitter
        self.stream_handler = SuggestResponseStreamHandler(socket_emitter or _NoOpEmitter())
        self.tool_handler = PlaybookToolHandler(socket_emitter)

    async def retrieve(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        fan_page_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        agent_response_id: str,
        input_messages: List[Dict[str, Any]],
        settings: Dict[str, Any],
        api_key: str,
        page_memory: str = "",
        user_memory: str = "",
    ) -> PlaybookRetrievalResult:
        """
        Run playbook selection agent loop; return result with system-reminder and accumulated messages.

        Args:
            conn: Database connection (same transaction as caller).
            user_id, fan_page_id, conversation_type, conversation_id, run_id: For context and socket emission.
            agent_response_id: For billing.
            input_messages: Prepared context messages (system + user/assistant).
            settings: LLM settings (model, reasoning, verbosity).
            api_key: API key for LLM.
            page_memory: Rendered page memory/policy text for playbook selection context.
            user_memory: Rendered user memory text for playbook selection context.

        Returns:
            PlaybookRetrievalResult with system_reminder (or None) and accumulated_messages for persistence.
        """
        empty_result = PlaybookRetrievalResult(system_reminder=None, accumulated_messages=[])

        try:
            page_admins = await get_facebook_page_admins_by_user_id(conn, user_id)
            page_admin_id = None
            for admin in page_admins or []:
                if str(admin.get("page_id")) == str(fan_page_id):
                    page_admin_id = str(admin.get("id"))
                    break
            if not page_admin_id:
                return empty_result

            assigned_ids = await get_assigned_playbook_ids(
                conn, page_admin_id, conversation_type
            )
            if not assigned_ids:
                return empty_result

            initial = build_initial_input_messages(
                input_messages,
                page_memory=page_memory,
                user_memory=user_memory,
            )
            if len(initial) <= 1:
                return empty_result

            log_playbook_retriever_input(
                user_id=user_id,
                fan_page_id=fan_page_id,
                conversation_type=conversation_type,
                agent_response_id=agent_response_id,
                input_messages=input_messages,
                settings=settings,
                llm_messages=initial,
            )

            model = settings.get("model", "gpt-5.2")
            reasoning = settings.get("reasoning", "low")
            reasoning_param = get_reasoning_param(model, reasoning)
            llm_call = LLM_call(api_key=api_key)

            context: List[Dict[str, Any]] = list(initial)
            search_count = 0
            playbook_cache: Dict[str, Dict[str, Any]] = {}
            selected_ids: List[str] = []
            tools = get_playbook_tool_definitions()
            accumulated_messages: List[MessageResponse] = []

            for iteration in range(MAX_ITERATIONS):
                if self.socket_emitter:
                    await self.socket_emitter.emit_iteration_started(
                        user_id=user_id,
                        conversation_type=conversation_type,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        iteration_index=iteration,
                        step=STEP,
                    )

                api_input = input_items_for_api(context)
                stream_result = await self.stream_handler.stream(
                    llm_call=llm_call,
                    user_id=user_id,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    iteration_index=iteration,
                    input_messages=api_input,
                    tools=tools,
                    model=model,
                    reasoning_param=reasoning_param,
                    verbosity="low",
                    step=STEP,
                )

                response_dict = stream_result.response_dict
                output_items = response_dict.get("output", [])

                await insert_openai_response_with_agent(
                    conn=conn,
                    user_id=user_id,
                    conversation_id=None,
                    branch_id=None,
                    agent_response_id=agent_response_id,
                    response_data=response_dict,
                    input_messages=api_input,
                    tools=tools,
                    model=model,
                    metadata={
                        "type": "playbook_selection_agent",
                        "iteration": iteration,
                    },
                )

                saw_select = False
                for item in output_items:
                    if item.get("type") != "function_call":
                        continue
                    name = item.get("name")
                    call_id = item.get("call_id", str(uuid.uuid4()))
                    arguments_str = item.get("arguments", "{}")
                    try:
                        arguments = json.loads(arguments_str)
                    except json.JSONDecodeError:
                        arguments = {}

                    context.append(
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": arguments_str,
                        }
                    )

                    if name == "search_playbooks":
                        ctx_out, playbook_cache, search_count, msgs = (
                            await self.tool_handler.handle_search(
                                conn=conn,
                                user_id=user_id,
                                conversation_type=conversation_type,
                                conversation_id=conversation_id,
                                run_id=run_id,
                                iteration_index=iteration,
                                call_id=call_id,
                                arguments=arguments,
                                search_count=search_count,
                                assigned_ids=assigned_ids,
                                agent_response_id=agent_response_id,
                                playbook_cache=playbook_cache,
                            )
                        )
                        accumulated_messages.extend(msgs)
                        context.append(ctx_out)
                        if search_count >= MAX_SEARCHES:
                            tools = get_playbook_tool_definitions_select_only()

                    elif name == "select_playbooks":
                        selected_ids, ctx_out, msgs = (
                            await self.tool_handler.handle_select(
                                user_id=user_id,
                                conversation_type=conversation_type,
                                conversation_id=conversation_id,
                                run_id=run_id,
                                iteration_index=iteration,
                                call_id=call_id,
                                arguments=arguments,
                            )
                        )
                        accumulated_messages.extend(msgs)
                        context.append(ctx_out)
                        saw_select = True
                        break

                if self.socket_emitter:
                    await self.socket_emitter.emit_iteration_done(
                        user_id=user_id,
                        conversation_type=conversation_type,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        iteration_index=iteration,
                        has_more=not saw_select,
                        step=STEP,
                    )

                if saw_select:
                    break

            chosen = [
                playbook_cache[pid] for pid in selected_ids if pid in playbook_cache
            ]
            if not chosen:
                return PlaybookRetrievalResult(
                    system_reminder=None,
                    accumulated_messages=accumulated_messages,
                )
            system_reminder = format_playbooks_as_system_reminder(chosen)
            return PlaybookRetrievalResult(
                system_reminder=system_reminder,
                accumulated_messages=accumulated_messages,
            )

        except Exception as e:
            logger.warning(
                "Playbook retrieval failed (continuing without playbooks): %s",
                e,
                exc_info=True,
            )
            return empty_result


class _NoOpEmitter:
    """No-op emitter when socket_emitter is None so stream_handler always has a valid target."""

    async def emit_reasoning_started(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def emit_reasoning_delta(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def emit_reasoning_done(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def emit_tool_call_started(self, *args: Any, **kwargs: Any) -> None:
        pass
