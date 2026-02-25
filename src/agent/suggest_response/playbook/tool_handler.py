"""Tool handler for playbook selection agent: dispatches to BaseTool (SearchPlaybooksTool, SelectPlaybooksTool)."""

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from src.agent.general_agent.context.function_output_normalizer import (
    normalize_function_output_to_api_format,
)
from src.agent.suggest_response.playbook.tools import (
    SearchPlaybooksTool,
    SelectPlaybooksTool,
)
from src.agent.suggest_response.socket.emitter import SuggestResponseSocketEmitter
from src.agent.tools.base import ToolCallContext
from src.api.openai_conversations.schemas import MessageResponse

STEP = "playbook_retrieval"

_search_tool = SearchPlaybooksTool()
_select_tool = SelectPlaybooksTool()


def _get_tool(name: str):
    """Return tool instance by name."""
    if name == "search_playbooks":
        return _search_tool
    if name == "select_playbooks":
        return _select_tool
    return None


def _make_function_call_message(
    conversation_id: str,
    call_id: str,
    name: str,
    arguments: Dict[str, Any],
) -> MessageResponse:
    """Build MessageResponse for a function_call (for persistence)."""
    current_time = int(time.time() * 1000)
    return MessageResponse(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        sequence_number=0,
        type="function_call",
        role="assistant",
        content=None,
        call_id=call_id,
        function_name=name,
        function_arguments=arguments,
        status="completed",
        metadata=None,
        created_at=current_time,
        updated_at=current_time,
    )


class PlaybookToolHandler:
    """Dispatch to playbook BaseTools; emit socket events; collect MessageResponse for persistence."""

    def __init__(
        self,
        socket_emitter: Optional[SuggestResponseSocketEmitter] = None,
    ) -> None:
        self.socket_emitter = socket_emitter

    async def handle_search(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        iteration_index: int,
        call_id: str,
        arguments: Dict[str, Any],
        search_count: int,
        assigned_ids: List[str],
        agent_response_id: str,
        playbook_cache: Dict[str, Dict[str, Any]],
    ) -> Tuple[
        Dict[str, Any],
        Dict[str, Dict[str, Any]],
        int,
        List[MessageResponse],
    ]:
        """Execute search_playbooks via SearchPlaybooksTool. Returns (context_output_dict, updated_cache, new_search_count, messages)."""
        tool = _get_tool("search_playbooks")
        if not tool:
            raise ValueError("search_playbooks tool not found")

        context = ToolCallContext(
            user_id=user_id,
            conv_id=conversation_id,
            branch_id="",
            agent_response_id=agent_response_id,
            call_id=call_id,
            tool_name="search_playbooks",
            arguments=arguments,
            playbook_assigned_ids=assigned_ids,
            playbook_agent_response_id=agent_response_id,
            playbook_cache=playbook_cache,
            playbook_search_count=search_count,
        )

        raw_result = await tool.execute(conn, context, arguments)
        result = tool.process_result(context, raw_result)

        meta = result.metadata or {}
        updated_cache = meta.get("updated_cache", playbook_cache)
        new_count = meta.get("new_search_count", search_count)

        if "output_for_llm" in meta:
            normalized = normalize_function_output_to_api_format(meta["output_for_llm"])
        else:
            normalized = normalize_function_output_to_api_format(meta.get("output_text", ""))
        ctx_out = {"type": "function_call_output", "call_id": call_id, "output": normalized}

        msg_fc = _make_function_call_message(
            conversation_id, call_id, "search_playbooks", arguments
        )
        if self.socket_emitter:
            await self.socket_emitter.emit_tool_result(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                run_id=run_id,
                iteration_index=iteration_index,
                msg_item=result.output_message.model_dump(mode="json"),
                step=STEP,
            )
        return ctx_out, updated_cache, new_count, [msg_fc, result.output_message]

    async def handle_select(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        iteration_index: int,
        call_id: str,
        arguments: Dict[str, Any],
    ) -> Tuple[List[str], Dict[str, Any], List[MessageResponse]]:
        """Process select_playbooks via SelectPlaybooksTool. Returns (selected_ids, context_output_dict, messages)."""
        tool = _get_tool("select_playbooks")
        if not tool:
            raise ValueError("select_playbooks tool not found")

        context = ToolCallContext(
            user_id=user_id,
            conv_id=conversation_id,
            branch_id="",
            agent_response_id="",
            call_id=call_id,
            tool_name="select_playbooks",
            arguments=arguments,
        )

        raw_result = await tool.execute(None, context, arguments)
        result = tool.process_result(context, raw_result)

        meta = result.metadata or {}
        selected_ids = meta.get("selected_ids", [])

        output_text = f"Selected {len(selected_ids)} playbook(s)."
        normalized = normalize_function_output_to_api_format(output_text)
        ctx_out = {"type": "function_call_output", "call_id": call_id, "output": normalized}

        msg_fc = _make_function_call_message(
            conversation_id, call_id, "select_playbooks", arguments
        )
        if self.socket_emitter:
            await self.socket_emitter.emit_tool_result(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                run_id=run_id,
                iteration_index=iteration_index,
                msg_item=result.output_message.model_dump(mode="json"),
                step=STEP,
            )
        return selected_ids, ctx_out, [msg_fc, result.output_message]
