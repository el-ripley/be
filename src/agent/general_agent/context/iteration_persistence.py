from typing import Any, Dict, List

import asyncpg

from src.api.openai_conversations.schemas import MessageResponse
from src.database.postgres.repositories.agent_queries import (
    insert_openai_response_with_agent,
    save_message_and_update_branch,
    update_agent_response_aggregates,
    update_agent_response_message_ids,
)
from src.utils.logger import get_logger

logger = get_logger()


class IterationPersistence:
    """Handle persistence of agent iterations to Postgres.

    Responsibilities:
    - Save a batch of temp MessageResponse objects as DB messages
    - Link them to the agent_response row
    - Insert the raw OpenAI response row
    - Update aggregates on the agent_response
    """

    async def save_iteration(
        self,
        conn: asyncpg.Connection,
        *,
        user_id: str,
        conversation_id: str,
        branch_id: str,
        agent_response_id: str,
        temp_messages: List[MessageResponse],
        openai_response_data: Dict[str, Any],
        is_final: bool,
        model: str,
        tools: List[Dict[str, Any]],
        input_messages: List[Dict[str, Any]],
    ) -> List[str]:
        """Persist a single iteration and return saved message IDs."""
        # 1. Save messages
        saved_message_ids = await self._save_messages_batch(
            conn, conversation_id, branch_id, temp_messages
        )

        # 2. Attach message IDs to agent_response
        await update_agent_response_message_ids(
            conn, agent_response_id, saved_message_ids
        )

        # 3. Extract status + error_details from response_data if present
        status = openai_response_data.pop("_status", "completed")
        error_details = openai_response_data.pop("_error_details", None)

        # 4. Insert OpenAI response row
        await insert_openai_response_with_agent(
            conn,
            user_id,
            conversation_id,
            branch_id,
            agent_response_id,
            openai_response_data,
            input_messages,
            tools,
            model,
            metadata=None,
            status=status,
            error_details=error_details,
        )

        # 5. Update aggregates (but don't finalize status here)
        await update_agent_response_aggregates(conn, agent_response_id)

        return saved_message_ids

    async def _save_messages_batch(
        self,
        conn: asyncpg.Connection,
        conversation_id: str,
        branch_id: str,
        temp_messages: List[MessageResponse],
    ) -> List[str]:
        """Mirror of AgentContextManager._save_messages_batch."""
        saved_message_ids: List[str] = []

        for temp_msg in temp_messages:
            try:
                content_payload = None
                reasoning_summary_payload = None
                function_name_payload = None
                function_arguments_payload = None
                function_output_payload = None
                call_id_payload = temp_msg.call_id
                web_search_action_payload = None

                if temp_msg.type in {"message", "user_input"}:
                    content_payload = temp_msg.content
                elif temp_msg.type == "reasoning":
                    reasoning_summary_payload = temp_msg.reasoning_summary
                elif temp_msg.type == "function_call":
                    function_name_payload = temp_msg.function_name
                    function_arguments_payload = temp_msg.function_arguments
                elif temp_msg.type == "function_call_output":
                    function_output_payload = temp_msg.function_output
                elif temp_msg.type == "web_search_call":
                    web_search_action_payload = temp_msg.web_search_action
                else:
                    content_payload = temp_msg.content

                message_id = await save_message_and_update_branch(
                    conn,
                    conversation_id,
                    branch_id,
                    temp_msg.role,
                    content_payload,
                    temp_msg.type,
                    reasoning_summary=reasoning_summary_payload,
                    function_name=function_name_payload,
                    function_arguments=function_arguments_payload,
                    function_output=function_output_payload,
                    call_id=call_id_payload,
                    status=temp_msg.status,
                    metadata=temp_msg.metadata,
                    message_id=temp_msg.id,
                    web_search_action=web_search_action_payload,
                )

                saved_message_ids.append(message_id)

            except Exception as exc:
                logger.error("Error saving temp message %s: %s", temp_msg.id, exc)

        return saved_message_ids


__all__ = ["IterationPersistence"]
