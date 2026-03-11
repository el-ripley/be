"""
Summarization orchestrator service.

Handles the orchestration of summarization process including:
- Token limit checking
- Calling SummarizerService
- Database operations (hiding messages, inserting summary)
- Emitting socket events for FE updates
"""

from typing import Any, Dict, List, Optional

from src.agent.general_agent.core.run_config import RunConfig
from src.agent.general_agent.summarization.summarizer_service import SummarizerService
from src.database.postgres.connection import async_db_transaction, get_async_connection
from src.database.postgres.repositories.agent_queries import (
    get_all_branch_messages,
    save_message_and_update_branch,
    upsert_message_mapping,
)
from src.database.postgres.repositories.agent_queries.agent_responses import (
    get_latest_openai_response_for_conversation,
)
from src.socket_service import SocketService
from src.utils.logger import get_logger

logger = get_logger()


class SummarizationOrchestrator:
    """Orchestrates summarization process with event emission."""

    def __init__(self, socket_service: SocketService):
        self.socket_service = socket_service

    async def check_and_trigger(
        self,
        user_id: str,
        conversation_id: str,
        branch_id: str,
        run_config: RunConfig,
        parent_agent_response_id: Optional[str] = None,
    ) -> None:
        """
        Check if context tokens exceed limit and trigger summarization if needed.

        Emits events:
        - summarization.started: When summarization begins
        - summarization.completed: When summarization succeeds
        - summarization.skipped: When summarization is not needed
        - summarization.failed: When summarization fails
        """
        # Calculate effective limit
        buffer_tokens = int(
            run_config.context_token_limit * run_config.context_buffer_percent / 100
        )
        effective_limit = run_config.context_token_limit - buffer_tokens

        # Get latest input_tokens from most recent openai_response for this conversation/branch
        async with get_async_connection() as conn:
            latest_response = await get_latest_openai_response_for_conversation(
                conn, conversation_id, branch_id=branch_id
            )

        if not latest_response:
            # No previous response, nothing to check
            await self._emit_skipped(
                user_id=user_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
                reason="No previous response found",
            )
            return

        input_tokens = latest_response.get("input_tokens", 0) or 0
        # Fallback: when input_tokens is 0 (e.g. from failed responses), estimate from total - output
        if input_tokens == 0:
            total = latest_response.get("total_tokens", 0) or 0
            output = latest_response.get("output_tokens", 0) or 0
            input_tokens = max(0, total - output)
        run_config.current_context_tokens = input_tokens

        logger.info(
            f"Context check: {input_tokens} tokens / {effective_limit} effective limit"
        )

        if input_tokens < effective_limit:
            # Under limit, no summarization needed
            await self._emit_skipped(
                user_id=user_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
                reason=f"Token count ({input_tokens}) below effective limit ({effective_limit})",
            )
            return

        logger.info(
            f"Context tokens ({input_tokens}) >= effective limit ({effective_limit}). Triggering summarization..."
        )

        # Emit started event
        await self._emit_started(
            user_id=user_id,
            conversation_id=conversation_id,
            branch_id=branch_id,
            tokens_before=input_tokens,
            effective_limit=effective_limit,
        )

        # Execute summarization
        try:
            await self._execute_summarization(
                user_id=user_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
                run_config=run_config,
                parent_agent_response_id=parent_agent_response_id,
            )
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            await self._emit_failed(
                user_id=user_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
                error=str(e),
            )

    async def _execute_summarization(
        self,
        user_id: str,
        conversation_id: str,
        branch_id: str,
        run_config: RunConfig,
        parent_agent_response_id: Optional[str] = None,
    ) -> None:
        """Execute summarization process."""
        # Get all messages from branch (excluding hidden ones)
        async with get_async_connection() as conn:
            branch_messages = await get_all_branch_messages(
                conn, branch_id, order="ASC"
            )

        if not branch_messages or len(branch_messages) < 6:
            # Not enough messages to summarize (need at least system + 4 recent + 1 to summarize)
            logger.info(
                f"Only {len(branch_messages) if branch_messages else 0} messages, skipping summarization"
            )
            await self._emit_skipped(
                user_id=user_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
                reason="not_enough_messages",
                message_count=len(branch_messages) if branch_messages else 0,
                minimum_required=6,
            )
            return

        # Convert to OpenAI format and extract IDs
        messages = []
        message_ids = []
        for msg in branch_messages:
            if msg.get("is_hidden"):
                continue  # Skip already hidden messages

            message_ids.append(msg["id"])
            # Convert to OpenAI format based on type
            openai_msg = self._convert_db_message_to_openai_format(msg)
            if openai_msg:
                messages.append(openai_msg)

        # Get effective context settings for summarizer model
        from src.agent.common.conversation_settings import (
            get_effective_context_settings,
        )

        async with get_async_connection() as conn:
            effective_context_settings = await get_effective_context_settings(
                user_id, conn
            )
        summarizer_model = effective_context_settings["summarizer_model"]
        summarizer = SummarizerService(model=summarizer_model)

        async with async_db_transaction() as conn:
            result_tuple = await summarizer.summarize(
                conn=conn,
                messages=messages,
                message_ids=message_ids,
                active_tab=run_config.active_tab,
                user_id=user_id,
                api_key=run_config.api_key,
                parent_agent_response_id=parent_agent_response_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
            )

        if not result_tuple:
            logger.info("Summarization returned no result, skipping")
            await self._emit_skipped(
                user_id=user_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
                reason="Summarizer service returned no result",
            )
            return

        result, agent_response_id = result_tuple

        # Get tokens after (estimate based on summary length)
        # We'll use a simple estimation: summary is typically 30% of original
        tokens_after = int(run_config.current_context_tokens * 0.3)

        async with async_db_transaction() as conn:
            # 1. Hide old messages using existing mechanism (openai_branch_message_mapping.is_hidden)
            hidden_count = 0
            for msg_id in result.messages_to_hide:
                if msg_id:
                    await upsert_message_mapping(
                        conn=conn,
                        message_id=msg_id,
                        branch_id=branch_id,
                        is_hidden=True,
                    )
                    hidden_count += 1

            # 2. Insert summary message into branch
            from src.agent.common.metadata_types import MessageMetadata

            summary_metadata: MessageMetadata = {"source": "summarization"}
            summary_msg_id = await save_message_and_update_branch(
                conn=conn,
                conversation_id=conversation_id,
                branch_id=branch_id,
                role="assistant",
                content=result.summary_text,
                message_type="summary",
                metadata=summary_metadata,
            )

            logger.info(
                f"Summarization complete. Hidden {hidden_count} messages, created summary message {summary_msg_id}"
            )

        # Emit completed event
        await self._emit_completed(
            user_id=user_id,
            conversation_id=conversation_id,
            branch_id=branch_id,
            summary_message_id=summary_msg_id,
            hidden_message_ids=result.messages_to_hide,
            tokens_after=tokens_after,
        )

    def _convert_db_message_to_openai_format(
        self, msg: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Convert database message to OpenAI format for summarizer."""
        msg_type = msg.get("type")
        role = msg.get("role")

        if msg_type == "message" or msg_type == "user_input":
            return {
                "role": role,
                "content": msg.get("content") or "",
            }
        elif msg_type == "function_call":
            return {
                "type": "function_call",
                "name": msg.get("function_name"),
                "arguments": msg.get("function_arguments"),
            }
        elif msg_type == "function_call_output":
            return {
                "type": "function_call_output",
                "output": msg.get("function_output"),
            }
        elif msg_type == "reasoning":
            return {
                "type": "reasoning",
                "summary": msg.get("reasoning_summary"),
            }
        elif msg_type == "summary":
            # Already a summary, include it
            return {
                "role": "assistant",
                "content": msg.get("content") or "",
            }

        return None

    async def _emit_started(
        self,
        user_id: str,
        conversation_id: str,
        branch_id: str,
        tokens_before: int,
        effective_limit: int,
    ) -> None:
        """Emit summarization.started event."""
        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conversation_id,
            branch_id=branch_id,
            event_name="summarization.started",
            msg_item={
                "tokens_before": tokens_before,
                "effective_limit": effective_limit,
            },
        )

    async def _emit_completed(
        self,
        user_id: str,
        conversation_id: str,
        branch_id: str,
        summary_message_id: str,
        hidden_message_ids: List[str],
        tokens_after: int,
    ) -> None:
        """Emit summarization.completed event."""
        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conversation_id,
            branch_id=branch_id,
            event_name="summarization.completed",
            msg_item={
                "summary_message_id": summary_message_id,
                "hidden_message_ids": hidden_message_ids,
                "tokens_after": tokens_after,
            },
        )

    async def _emit_skipped(
        self,
        user_id: str,
        conversation_id: str,
        branch_id: str,
        reason: str,
        message_count: Optional[int] = None,
        minimum_required: Optional[int] = None,
    ) -> None:
        """Emit summarization.skipped event."""
        event_name = "summarization.skipped"
        msg_item = {"reason": reason}

        # Special case for not_enough_messages
        if reason == "not_enough_messages" and message_count is not None:
            event_name = "summarization.skipped.not_enough_messages"
            msg_item = {
                "message_count": message_count,
                "minimum_required": minimum_required or 6,
            }

        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conversation_id,
            branch_id=branch_id,
            event_name=event_name,
            msg_item=msg_item,
        )

    async def _emit_failed(
        self,
        user_id: str,
        conversation_id: str,
        branch_id: str,
        error: str,
    ) -> None:
        """Emit summarization.failed event."""
        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conversation_id,
            branch_id=branch_id,
            event_name="summarization.failed",
            msg_item={"error": error},
        )
