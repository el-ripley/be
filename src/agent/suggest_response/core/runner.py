"""Suggest Response Runner - generates response suggestions using LLM."""

import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from src.services.notifications.escalation_trigger import (
        EscalationNotificationTrigger,
    )

from src.agent.common.agent_types import AGENT_TYPE_SUGGEST_RESPONSE_AGENT
from src.agent.common.api_key_resolver_service import get_system_api_key
from src.agent.common.conversation_settings import (
    get_default_settings,
    get_reasoning_param,
    normalize_settings,
)
from src.agent.core.llm_call import LLM_call
from src.agent.suggest_response.context.context_builder import (
    SuggestResponseContextBuilder,
)
from src.agent.suggest_response.core.iteration_runner import (
    SuggestResponseIterationRunner,
)
from src.agent.suggest_response.core.run_config import LLMResult, PreparedContext
from src.agent.suggest_response.playbook import PlaybookRetriever
from src.agent.suggest_response.socket.emitter import SuggestResponseSocketEmitter
from src.agent.suggest_response.socket.stream_handler import (
    SuggestResponseStreamHandler,
)
from src.agent.suggest_response.tools.tool_executor import SuggestResponseToolExecutor
from src.agent.suggest_response.tools.tool_registry import SuggestResponseToolRegistry
from src.agent.suggest_response.utils.message_accumulator import (
    SuggestResponseMessageAccumulator,
)
from src.agent.suggest_response.utils.persistence import SuggestResponsePersistence
from src.agent.suggest_response.utils.prompt_logger import log_suggest_response_prompts
from src.agent.suggest_response.utils.response_parser import SuggestResponseParser
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories import (
    create_agent_response,
    get_agent_settings,
    get_facebook_page_admins_by_user_id,
)
from src.database.postgres.repositories.facebook_queries.messages.conversations import (
    get_conversation_with_details,
)
from src.database.postgres.repositories.media_assets_queries import (
    get_media_assets_by_ids,
)
from src.database.postgres.utils import generate_uuid
from src.redis_client.redis_suggest_response_cache import RedisSuggestResponseCache
from src.socket_service import SocketService
from src.utils.logger import get_logger

logger = get_logger()


class InsufficientBalanceError(Exception):
    """Exception raised when user has insufficient balance to use AI."""

    pass


def _input_messages_to_redis_tuples(
    input_messages: List[Dict[str, Any]],
) -> List[Tuple[str, Dict[str, Any]]]:
    """Convert input_messages to (msg_id, message_dict) for Redis temp context."""
    result: List[Tuple[str, Dict[str, Any]]] = []
    for idx, msg in enumerate(input_messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        msg_id = "__system__" if (idx == 0 and role == "system") else generate_uuid()
        message_dict: Dict[str, Any] = {
            "type": "message",
            "role": role,
            "content": content,
        }
        result.append((msg_id, message_dict))
    return result


class SuggestResponseRunner:
    """Runner for suggest response agent."""

    def __init__(
        self,
        socket_service: SocketService,
        redis_agent_manager: Any,
        context_builder: Optional[SuggestResponseContextBuilder] = None,
        stream_handler: Optional[SuggestResponseStreamHandler] = None,
        parser: Optional[SuggestResponseParser] = None,
        persistence: Optional[SuggestResponsePersistence] = None,
        suggest_response_cache: Optional[RedisSuggestResponseCache] = None,
        escalation_trigger: Optional["EscalationNotificationTrigger"] = None,
        playbook_retriever: Optional[PlaybookRetriever] = None,
    ):
        self.max_iterations = 15
        self.socket_emitter = SuggestResponseSocketEmitter(socket_service)
        self.context_builder = context_builder or SuggestResponseContextBuilder()
        self.stream_handler = stream_handler or SuggestResponseStreamHandler(
            self.socket_emitter
        )
        self.parser = parser or SuggestResponseParser()
        self.persistence = persistence or SuggestResponsePersistence()
        self.suggest_response_cache = suggest_response_cache
        self.redis_agent_manager = redis_agent_manager
        self.playbook_retriever = playbook_retriever or PlaybookRetriever(
            socket_emitter=self.socket_emitter
        )

        tool_registry = SuggestResponseToolRegistry()
        self.tool_registry = tool_registry
        self.tool_executor = SuggestResponseToolExecutor(
            registry=tool_registry,
            socket_emitter=self.socket_emitter,
            escalation_trigger=escalation_trigger,
        )
        self.iteration_runner = SuggestResponseIterationRunner(
            stream_handler=self.stream_handler,
            tool_executor=self.tool_executor,
            redis_manager=redis_agent_manager,
        )

    async def run(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        trigger_type: str,
        trigger_action: str,
        num_suggestions: int,
        settings: Dict[str, Any],
        facebook_page_scope_user_id: Optional[str] = None,
        bypass_hash_check: bool = False,
        suggest_response_cache: Optional[RedisSuggestResponseCache] = None,
        delivery_mode: str = "suggest",
        hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Pure agent logic - no lock, balance check done by orchestrator.

        Args:
            bypass_hash_check: If True, skip hash check (for manual trigger)
            suggest_response_cache: Cache for hash operations (optional)

        Returns:
            Dict with history_id, suggestions, suggestion_count, skipped, skip_reason, task_summary
        """
        run_id = generate_uuid()
        try:
            # Resolve PSID for messages when missing (required for RLS on page_scope_user_memory)
            if conversation_type == "messages" and not facebook_page_scope_user_id:
                async with async_db_transaction() as conn:
                    conv = await get_conversation_with_details(conn, conversation_id)
                    if conv:
                        facebook_page_scope_user_id = conv.get(
                            "facebook_page_scope_user_id"
                        )
                        if facebook_page_scope_user_id:
                            logger.debug(
                                "Resolved missing PSID for suggest_response from conversation: %s",
                                conversation_id,
                            )

            # Prepare context with provided settings and num_suggestions
            prepared = await self._prepare_context(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                fan_page_id=fan_page_id,
                facebook_page_scope_user_id=facebook_page_scope_user_id,
                settings_override=settings,
                num_suggestions_override=num_suggestions,
                trigger_type=trigger_type,
                trigger_action=trigger_action,
                delivery_mode=delivery_mode,
                hint=hint,
            )

            # Hash check (bypass for manual trigger)
            conversation_content = self._extract_conversation_content(
                prepared.input_messages
            )
            current_hash = (
                suggest_response_cache.compute_hash(conversation_content)
                if conversation_content and suggest_response_cache
                else ""
            )
            if not bypass_hash_check and suggest_response_cache and current_hash:
                if await suggest_response_cache.should_skip_generation(
                    conversation_type, conversation_id, current_hash
                ):
                    return {
                        "history_id": None,
                        "suggestions": [],
                        "suggestion_count": 0,
                        "skipped": True,
                        "skip_reason": "hash_unchanged",
                    }

            # Create agent_response early so playbook retrieval (LLM + embedding) can bill to it
            async with async_db_transaction() as conn:
                agent_response_id = await create_agent_response(
                    conn=conn,
                    user_id=user_id,
                    conversation_id=None,
                    branch_id=None,
                    agent_type=AGENT_TYPE_SUGGEST_RESPONSE_AGENT,
                )

            await self.socket_emitter.emit_run_started(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                run_id=run_id,
            )

            await self.socket_emitter.emit_step_started(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                run_id=run_id,
                step="playbook_retrieval",
            )
            playbook_result = None
            try:
                async with async_db_transaction() as conn:
                    playbook_result = await self.playbook_retriever.retrieve(
                        conn=conn,
                        user_id=user_id,
                        fan_page_id=fan_page_id,
                        conversation_type=conversation_type,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        agent_response_id=agent_response_id,
                        input_messages=prepared.input_messages,
                        settings=prepared.settings,
                        api_key=prepared.api_key,
                        page_memory=prepared.metadata.get("page_memory_text", ""),
                        user_memory=prepared.metadata.get("user_memory_text", ""),
                    )
            finally:
                await self.socket_emitter.emit_step_completed(
                    user_id=user_id,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    step="playbook_retrieval",
                    result={
                        "has_playbooks": playbook_result is not None
                        and playbook_result.system_reminder is not None
                    },
                )

            if playbook_result and playbook_result.system_reminder:
                self._inject_playbook_into_messages(
                    prepared.input_messages, playbook_result.system_reminder
                )

            log_suggest_response_prompts(
                input_messages=prepared.input_messages,
                metadata=prepared.metadata,
                prefix=f"suggest_response_{conversation_type}",
            )

            await self.socket_emitter.emit_step_started(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                run_id=run_id,
                step="response_generation",
            )
            try:
                llm_result = await self._run_iteration_loop(
                    run_id=run_id,
                    prepared=prepared,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    facebook_page_scope_user_id=facebook_page_scope_user_id,
                )
            finally:
                await self.socket_emitter.emit_step_completed(
                    user_id=user_id,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    step="response_generation",
                )

            tools = self.tool_registry.get_tool_definitions(
                conversation_type,
                prepared.num_suggestions,
            )
            playbook_messages = (
                playbook_result.accumulated_messages if playbook_result else None
            )
            history_id = await self.persistence.save_result(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                prepared=prepared,
                llm_result=llm_result,
                trigger_type=trigger_type,
                tools=tools,
                accumulated_messages=llm_result.accumulated_messages,
                agent_response_id=agent_response_id,
                playbook_messages=playbook_messages,
            )

            await self.socket_emitter.emit_run_completed(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                run_id=run_id,
                history_id=history_id,
                suggestions=llm_result.suggestions_list,
            )

            if suggest_response_cache and current_hash:
                await suggest_response_cache.set_content_hash(
                    conversation_type, conversation_id, current_hash
                )

            return {
                "history_id": history_id,
                "suggestions": llm_result.suggestions_list,
                "suggestion_count": len(llm_result.suggestions_list),
                "skipped": False,
                "playbook_system_reminder": (
                    playbook_result.system_reminder if playbook_result else None
                ),
            }

        except Exception as e:
            logger.error(f"Error in suggest response run: {str(e)}")
            error_str = str(e).lower()
            code = getattr(e, "code", None) or (
                "MAX_ITERATIONS_EXHAUSTED"
                if "max_iterations_exhausted" in error_str
                else "INCOMPLETE"
                if "incomplete" in error_str
                else "ERROR"
            )
            await self.socket_emitter.emit_run_error(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                run_id=run_id,
                error=str(e),
                code=code,
            )
            raise

    async def _prepare_context(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        facebook_page_scope_user_id: Optional[str],
        settings_override: Optional[Dict[str, Any]] = None,
        num_suggestions_override: Optional[int] = None,
        trigger_type: Optional[str] = None,
        trigger_action: str = "routine_check",
        delivery_mode: str = "suggest",
        hint: Optional[str] = None,
    ) -> PreparedContext:
        """
        Prepare all context needed for LLM call.
        Single transaction for all read operations.
        Uses settings_override/num_suggestions_override if provided.
        """
        api_key = get_system_api_key()

        async with async_db_transaction() as conn:
            if settings_override is not None:
                settings = normalize_settings(settings_override)
                num_suggestions = num_suggestions_override or 3
            else:
                agent_settings_record = await get_agent_settings(conn, user_id)
                if agent_settings_record:
                    settings = normalize_settings(
                        agent_settings_record.get("settings", {})
                    )
                    num_suggestions = agent_settings_record.get(
                        "num_suggest_response", 3
                    )
                else:
                    settings = get_default_settings()
                    num_suggestions = 3

            # Build context (prompts + conversation data)
            try:
                input_messages, metadata = await self.context_builder.build_context(
                    conn=conn,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    fan_page_id=fan_page_id,
                    owner_user_id=user_id,
                    facebook_page_scope_user_id=facebook_page_scope_user_id,
                    delivery_mode=delivery_mode,
                    trigger_action=trigger_action,
                    hint=hint,
                )
            except Exception:
                raise

            # Verify user has access to this page (permission check)
            page_admins = await get_facebook_page_admins_by_user_id(conn, user_id)
            has_access = any(
                admin.get("page_id") == fan_page_id for admin in page_admins
            )

            if not has_access:
                raise ValueError(
                    f"User {user_id} is not an admin of page {fan_page_id}"
                )

        prepared = PreparedContext(
            input_messages=input_messages,
            metadata=metadata,
            settings=settings,
            num_suggestions=num_suggestions,
            user_id=user_id,
            fan_page_id=fan_page_id,
            api_key=api_key,
        )

        return prepared

    @staticmethod
    def _extract_conversation_content(input_messages: List[Dict[str, Any]]) -> str:
        """Extract conversation content for hashing.

        For comments: looks for the user message containing <conversation_data>.
        For messages (turn-based): concatenates all non-system message content.
        Handles both string and array-of-objects content formats.
        """

        def _content_to_str(content: Any) -> str:
            """Convert content (string or array-of-objects) to a single string."""
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n\n".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("text")
                )
            return str(content) if content else ""

        # First try comments format: look for <conversation_data> wrapper
        for msg in input_messages:
            if msg.get("role") != "user":
                continue
            content_str = _content_to_str(msg.get("content") or "")
            if "<conversation_data>" in content_str:
                return content_str

        # Messages format (turn-based): hash all non-system messages
        parts = []
        for msg in input_messages:
            if msg.get("role") == "system":
                continue
            content_str = _content_to_str(msg.get("content") or "")
            if content_str:
                parts.append(content_str)
        return "\n".join(parts)

    @staticmethod
    def _inject_playbook_into_messages(
        input_messages: List[Dict[str, Any]], playbook_block: str
    ) -> None:
        """Append playbook system-reminder to the last user message. Mutates input_messages."""
        if not playbook_block or not input_messages:
            return
        for i in range(len(input_messages) - 1, -1, -1):
            if input_messages[i].get("role") == "user":
                content = input_messages[i].get("content")
                if isinstance(content, list):
                    content.append({"type": "input_text", "text": playbook_block})
                elif isinstance(content, str):
                    input_messages[i]["content"] = content + "\n\n" + playbook_block
                else:
                    input_messages[i]["content"] = playbook_block
                return

    async def _resolve_suggestion_media(
        self,
        suggestions: List[Dict[str, Any]],
        user_id: str,
        conversation_type: str,
        message_ref_map: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Resolve media_ids and reply_to_ref in suggestions for downstream delivery.

        Converts agent's internal format (media_ids/attachment_media_id, reply_to_ref)
        to downstream format (image_urls/attachment_url, reply_to_message_id) with valid URLs.

        Args:
            suggestions: Raw suggestions from parser (with media_ids, reply_to_ref)
            user_id: Owner user ID for media asset lookup
            conversation_type: 'messages' or 'comments'
            message_ref_map: Mapping of "#N" → Facebook message ID for reply resolution

        Returns:
            Suggestions in downstream format with resolved URLs and message IDs
        """
        ref_map = message_ref_map or {}

        # Collect all unique media_ids across suggestions
        all_media_ids: set = set()
        for s in suggestions:
            if conversation_type == "messages":
                for mid in s.get("media_ids") or []:
                    if mid:
                        all_media_ids.add(mid)
            else:
                attachment_mid = s.get("attachment_media_id")
                if attachment_mid:
                    all_media_ids.add(attachment_mid)

        # Resolve media_ids → s3_urls via DB lookup
        media_url_map: Dict[str, str] = {}
        if all_media_ids:
            try:
                async with async_db_transaction() as conn:
                    media_assets = await get_media_assets_by_ids(
                        conn, list(all_media_ids), user_id
                    )
                    for asset in media_assets:
                        s3_url = asset.get("s3_url")
                        if s3_url:
                            media_url_map[str(asset["id"])] = s3_url
            except Exception as e:
                logger.error(
                    f"Failed to resolve media_ids for suggestions: {e}",
                    exc_info=True,
                )

        # Build resolved suggestions in downstream format
        resolved: List[Dict[str, Any]] = []
        for s in suggestions:
            if conversation_type == "messages":
                # Resolve media_ids → image_urls
                raw_ids = s.get("media_ids") or []
                image_urls = []
                for mid in raw_ids:
                    url = media_url_map.get(mid)
                    if url:
                        image_urls.append(url)
                    else:
                        logger.warning(
                            "media_id %s not found or has no s3_url, skipping",
                            mid,
                        )

                # Resolve reply_to_ref (#N) → reply_to_message_id (Facebook mid)
                reply_ref = s.get("reply_to_ref")
                reply_to_message_id = None
                if reply_ref:
                    normalized = reply_ref.strip()
                    if not normalized.startswith("#"):
                        normalized = f"#{normalized}"
                    reply_to_message_id = ref_map.get(normalized)
                    if not reply_to_message_id:
                        logger.warning(
                            "reply_to_ref %s not found in message_ref_map, ignoring",
                            reply_ref,
                        )

                resolved.append(
                    {
                        "message": s["message"],
                        "image_urls": image_urls if image_urls else None,
                        "video_url": s.get("video_url"),
                        "reply_to_message_id": reply_to_message_id,
                    }
                )
            else:
                # Resolve attachment_media_id → attachment_url
                attachment_mid = s.get("attachment_media_id")
                attachment_url = None
                if attachment_mid:
                    url = media_url_map.get(attachment_mid)
                    if url:
                        attachment_url = url
                    else:
                        logger.warning(
                            "attachment_media_id %s not found or has no s3_url, skipping",
                            attachment_mid,
                        )
                resolved.append(
                    {
                        "message": s["message"],
                        "attachment_url": attachment_url,
                    }
                )

        return resolved

    async def _run_iteration_loop(
        self,
        run_id: str,
        prepared: PreparedContext,
        conversation_type: str,
        conversation_id: str,
        facebook_page_scope_user_id: Optional[str],
    ) -> LLMResult:
        """Run multi-turn iteration loop until generate_suggestions or complete_task is called."""
        model = prepared.settings.get("model", "gpt-5.2")
        reasoning = prepared.settings.get("reasoning", "low")
        verbosity = prepared.settings.get("verbosity", "low")
        reasoning_param = get_reasoning_param(model, reasoning)
        tools = self.tool_registry.get_tool_definitions(
            conversation_type,
            prepared.num_suggestions,
        )
        llm_call = LLM_call(api_key=prepared.api_key)

        redis_tuples = _input_messages_to_redis_tuples(prepared.input_messages)
        await self.redis_agent_manager.set_temp_context(
            user_id=prepared.user_id,
            conversation_id=conversation_id,
            agent_resp_id=run_id,
            messages=redis_tuples,
        )

        start_time_ms = int(time.time() * 1000)
        last_response_dict: Optional[Dict[str, Any]] = None
        accumulator = SuggestResponseMessageAccumulator()

        try:
            for iteration in range(self.max_iterations):
                temp_context = await self.redis_agent_manager.get_temp_context(
                    user_id=prepared.user_id,
                    conversation_id=conversation_id,
                    agent_resp_id=run_id,
                )
                if not temp_context:
                    raise RuntimeError("Temp context lost during iteration")

                await self.socket_emitter.emit_iteration_started(
                    user_id=prepared.user_id,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    iteration_index=iteration,
                    step="response_generation",
                )

                result = await self.iteration_runner.run(
                    user_id=prepared.user_id,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    fan_page_id=prepared.fan_page_id,
                    page_scope_user_id=facebook_page_scope_user_id,
                    run_id=run_id,
                    agent_response_id=run_id,
                    temp_context=temp_context,
                    tools=tools,
                    llm_call=llm_call,
                    model=model,
                    reasoning_param=reasoning_param,
                    verbosity=verbosity,
                    current_iteration=iteration,
                    max_iteration=self.max_iterations,
                    accumulator=accumulator,
                    num_suggestions=prepared.num_suggestions,
                    step="response_generation",
                )

                has_more = not result.should_stop
                await self.socket_emitter.emit_iteration_done(
                    user_id=prepared.user_id,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    iteration_index=iteration,
                    has_more=has_more,
                    step="response_generation",
                )

                last_response_dict = result.response_dict
                if result.should_stop:
                    break
            else:
                # Loop exhausted without breaking (no terminal tool called)
                logger.warning(
                    "Max iterations (%d) exhausted without calling terminal tool for run_id=%s",
                    self.max_iterations,
                    run_id,
                )
                raise RuntimeError(
                    f"MAX_ITERATIONS_EXHAUSTED: Agent used all {self.max_iterations} "
                    "iterations without calling generate_suggestions or complete_task"
                )
        finally:
            await self.redis_agent_manager.delete_temp_context(
                user_id=prepared.user_id,
                conversation_id=conversation_id,
                agent_resp_id=run_id,
            )

        if not last_response_dict:
            raise RuntimeError("No response from LLM")

        suggestions_list, _ = self.parser.parse_tool_call_response(
            last_response_dict, conversation_type
        )

        # Resolve media_ids → s3_urls and reply_to_ref → reply_to_message_id
        suggestions_list = await self._resolve_suggestion_media(
            suggestions_list,
            prepared.user_id,
            conversation_type,
            message_ref_map=prepared.metadata.get("message_ref_map"),
        )

        end_time_ms = int(time.time() * 1000)
        latency_ms = end_time_ms - start_time_ms

        response_data = last_response_dict.copy()
        response_data["id"] = generate_uuid()
        response_data["created"] = start_time_ms
        response_data["latency_ms"] = latency_ms

        return LLMResult(
            suggestions_list=suggestions_list,
            response_data=response_data,
            latency_ms=latency_ms,
            accumulated_messages=accumulator.to_sorted_messages(),
        )
