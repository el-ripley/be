"""Persistence service for Suggest Response results."""

import json
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.agent.common.agent_types import AGENT_TYPE_SUGGEST_RESPONSE_AGENT
from src.api.openai_conversations.schemas import MessageResponse
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories import (
    create_agent_response,
    insert_openai_response_with_agent,
    finalize_agent_response,
    create_suggest_response_history,
    create_suggest_response_message,
)

if TYPE_CHECKING:
    from src.agent.suggest_response.core.run_config import (
        PreparedContext,
        LLMResult,
    )


class SuggestResponsePersistence:
    """Handle database operations for suggest response results."""

    async def save_result(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        prepared: "PreparedContext",
        llm_result: "LLMResult",
        trigger_type: str,
        tools: List[Dict[str, Any]],
        accumulated_messages: Optional[List[MessageResponse]] = None,
        agent_response_id: Optional[str] = None,
        playbook_messages: Optional[List[MessageResponse]] = None,
    ) -> str:
        """
        Save all results to database.
        Single transaction for all write operations.

        Args:
            user_id: User ID
            conversation_type: 'messages' or 'comments'
            conversation_id: Conversation ID
            prepared: PreparedContext with input messages, metadata, settings
            llm_result: LLMResult with suggestions and response data
            trigger_type: 'user' or 'auto'
            tools: List of tool definitions used in LLM call
            accumulated_messages: Optional list of all messages from iterations (reasoning, tool calls, outputs).
                When provided, these are saved to suggest_response_message instead of extracting from final response_data.
            agent_response_id: Optional. When provided (e.g. created earlier for playbook retrieval billing),
                skip create_agent_response and use this ID. All costs aggregate under this record.
            playbook_messages: Optional list of messages from playbook_retrieval step. When provided, saved first
                with step='playbook_retrieval' before response_generation messages.

        Returns:
            history_id as string
        """
        async with async_db_transaction() as conn:
            # Use existing agent_response_id or create one
            if agent_response_id is None:
                agent_response_id = await create_agent_response(
                    conn=conn,
                    user_id=user_id,
                    conversation_id=None,  # NULL for standalone suggest_response_agent
                    branch_id=None,  # NULL for standalone suggest_response_agent
                    agent_type=AGENT_TYPE_SUGGEST_RESPONSE_AGENT,
                )

            # Save openai_response
            await insert_openai_response_with_agent(
                conn=conn,
                user_id=user_id,
                conversation_id=None,  # NULL for standalone suggest_response_agent
                branch_id=None,  # NULL for standalone suggest_response_agent
                agent_response_id=agent_response_id,
                response_data=llm_result.response_data,
                input_messages=prepared.input_messages,
                tools=tools,
                model=prepared.settings.get("model", "gpt-5-mini"),
            )

            # Finalize agent_response
            await finalize_agent_response(conn, agent_response_id)
            # Deduct credits after finalization
            from src.billing.credit_service import deduct_credits_after_agent

            await deduct_credits_after_agent(conn, agent_response_id)

            # Save to suggest_response_history
            history_record = await create_suggest_response_history(
                conn=conn,
                user_id=prepared.user_id,
                fan_page_id=prepared.fan_page_id,
                conversation_type=conversation_type,
                facebook_conversation_messages_id=(
                    conversation_id if conversation_type == "messages" else None
                ),
                facebook_conversation_comments_id=(
                    conversation_id if conversation_type == "comments" else None
                ),
                latest_item_id=prepared.metadata.get("latest_item_id", ""),
                latest_item_facebook_time=prepared.metadata.get(
                    "latest_item_facebook_time", 0
                ),
                page_prompt_id=prepared.metadata.get("page_prompt_id"),
                page_scope_user_prompt_id=prepared.metadata.get(
                    "page_scope_user_prompt_id"
                ),
                suggestions=llm_result.suggestions_list,
                agent_response_id=agent_response_id,
                trigger_type=trigger_type,
            )

            history_id = str(history_record.get("id", ""))

            # Save message items: playbook first (step=playbook_retrieval), then response_generation
            message_items: List[Dict[str, Any]] = []
            if playbook_messages:
                message_items.extend(
                    self._convert_accumulated_messages_to_items(
                        playbook_messages, step="playbook_retrieval"
                    )
                )
            if accumulated_messages:
                message_items.extend(
                    self._convert_accumulated_messages_to_items(
                        accumulated_messages, step="response_generation"
                    )
                )
            if not message_items:
                message_items = self._extract_message_items(llm_result.response_data)
            if message_items:
                # Renumber sequence_number to be contiguous
                for seq, item in enumerate(message_items):
                    item["sequence_number"] = seq
                await self._save_message_items(conn, history_id, message_items)

        return history_id

    def _convert_accumulated_messages_to_items(
        self,
        messages: List[MessageResponse],
        step: str = "response_generation",
    ) -> List[Dict[str, Any]]:
        """Convert accumulated MessageResponse list to suggest_response_message record format."""
        items = []
        for seq, msg in enumerate(messages):
            items.append(
                {
                    "sequence_number": seq,
                    "role": msg.role,
                    "type": msg.type,
                    "content": msg.content,
                    "reasoning_summary": msg.reasoning_summary,
                    "call_id": msg.call_id,
                    "function_name": msg.function_name,
                    "function_arguments": msg.function_arguments,
                    "function_output": msg.function_output,
                    "web_search_action": msg.web_search_action,
                    "metadata": msg.metadata,
                    "status": msg.status or "completed",
                    "step": step,
                }
            )
        return items

    def _extract_message_items(
        self, response_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Extract message items from OpenAI response output.
        Converts output items to suggest_response_message format.
        """
        output = response_data.get("output") or []
        items = []
        for seq, item in enumerate(output):
            item_type = item.get("type")
            if not item_type:
                continue

            role = (
                "assistant" if item_type in ("reasoning", "function_call") else "tool"
            )
            # API uses "summary" for reasoning items; we store as reasoning_summary
            reasoning_summary = item.get("reasoning_summary") or (
                item.get("summary") if item_type == "reasoning" else None
            )
            record = {
                "sequence_number": seq,
                "role": role,
                "type": item_type,
                "content": item.get("content"),
                "reasoning_summary": reasoning_summary,
                "call_id": item.get("call_id"),
                "function_name": (
                    item.get("name") if item_type == "function_call" else None
                ),
                "function_arguments": (
                    item.get("arguments") if item_type == "function_call" else None
                ),
                "function_output": (
                    item.get("output") or item.get("content")
                    if item_type == "function_call_output"
                    else None
                ),
                "web_search_action": item.get("web_search_action"),
                "metadata": item.get("metadata"),
                "status": "completed",
                "step": "response_generation",
            }
            if item_type == "function_call" and isinstance(
                record["function_arguments"], str
            ):
                try:
                    record["function_arguments"] = json.loads(
                        record["function_arguments"]
                    )
                except (json.JSONDecodeError, TypeError):
                    record["function_arguments"] = None
            items.append(record)

        return items

    async def _save_message_items(
        self,
        conn: Any,
        history_id: str,
        message_items: List[Dict[str, Any]],
    ) -> None:
        """Save message items to suggest_response_message table."""
        for item in message_items:
            await create_suggest_response_message(
                conn=conn,
                history_id=history_id,
                sequence_number=item["sequence_number"],
                role=item["role"],
                type=item["type"],
                content=item.get("content"),
                reasoning_summary=item.get("reasoning_summary"),
                call_id=item.get("call_id"),
                function_name=item.get("function_name"),
                function_arguments=item.get("function_arguments"),
                function_output=item.get("function_output"),
                web_search_action=item.get("web_search_action"),
                metadata=item.get("metadata"),
                status=item.get("status"),
                step=item.get("step", "response_generation"),
            )
