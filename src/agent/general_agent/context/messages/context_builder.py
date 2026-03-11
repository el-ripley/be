from typing import Any, Dict, List, Optional, Tuple, Union

import asyncpg

from src.agent.general_agent.context.messages.system_prompt import BASE_SYSTEM_PROMPT
from src.agent.general_agent.context.messages.system_prompt_builder import (
    SystemPromptBuilder,
)
from src.api.openai_conversations.schemas import MessageResponse
from src.database.postgres.connection import get_async_connection
from src.database.postgres.repositories.agent_queries import (
    get_all_branch_messages,
    get_conversation,
)
from src.utils.logger import get_logger

from .image_processor import ImageProcessor
from .message_converter import MessageConverter, OpenAIMessageItem

logger = get_logger()


class ContextBuilder:
    """High-level orchestration for building OpenAI-ready conversation context.

    Responsibilities:
    - Resolve current branch for a conversation
    - Load branch messages from DB
    - Convert MessageResponse objects → OpenAI message items
    - Build system prompt via SystemPromptBuilder
    """

    def __init__(self) -> None:
        self.system_prompt_builder = SystemPromptBuilder()
        self.image_processor = ImageProcessor()
        self.message_converter = MessageConverter(self.image_processor)

    async def get_current_branch_id_for_conversation(
        self,
        conversation_id: str,
        conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[str]:
        try:
            if conn is None:
                async with get_async_connection() as db_conn:
                    conversation = await get_conversation(db_conn, conversation_id)
            else:
                conversation = await get_conversation(conn, conversation_id)

            if conversation and conversation.current_branch_id:
                return conversation.current_branch_id
        except Exception as exc:
            logger.error("Error getting current branch from DB: %s", exc)
        return None

    async def build_context(
        self,
        conversation_id: str,
        *,
        conn: Optional[asyncpg.Connection] = None,
        with_ids: bool = False,
        user_id: Optional[str] = None,
        current_iteration: int = 0,
        max_iteration: int = 20,
    ) -> Union[List[OpenAIMessageItem], List[Tuple[str, OpenAIMessageItem]]]:
        """Build OpenAI-ready messages for the current branch of a conversation.

        Always prepends the system prompt with live data (if user_id provided).
        """
        messages: List[Union[OpenAIMessageItem, Tuple[str, OpenAIMessageItem]]] = []
        current_branch_id = await self.get_current_branch_id_for_conversation(
            conversation_id, conn=conn
        )

        # Get conversation history only (no system message from DB)
        conversation_history = await self._build_branch_messages(
            current_branch_id, conn=conn, with_ids=with_ids
        )

        # Build system prompt with live data if user_id and conn provided
        if user_id and conn:
            from src.agent.common.conversation_settings import (
                get_default_settings,
                normalize_settings,
            )
            from src.agent.utils import ensure_content_items
            from src.database.postgres.repositories.agent_queries import (
                get_conversation_settings,
            )

            # Get model name from conversation settings
            conversation_settings = await get_conversation_settings(
                conn, conversation_id
            )
            if conversation_settings:
                settings = normalize_settings(conversation_settings)
            else:
                settings = get_default_settings()
            model_name = settings.get("model", "gpt-5-mini")

            system_prompt_content = await self.system_prompt_builder.build_prompt(
                conn=conn,
                user_id=user_id,
                model_name=model_name,
            )
        else:
            from src.agent.utils import ensure_content_items

            # Fallback to base system prompt
            system_prompt_content = BASE_SYSTEM_PROMPT

        from src.agent.utils import ensure_content_items

        system_message: Dict[str, Any] = {
            "role": "system",
            "content": ensure_content_items(system_prompt_content, "system"),
        }

        if with_ids:
            # Use special ID for system message (not from DB)
            messages.append(("__system__", system_message))
        else:
            messages.append(system_message)

        messages.extend(conversation_history)
        return messages

    async def _build_branch_messages(
        self,
        branch_id: Optional[str],
        *,
        conn: Optional[asyncpg.Connection] = None,
        with_ids: bool = False,
    ) -> List[Union[OpenAIMessageItem, Tuple[str, OpenAIMessageItem]]]:
        """Build conversation history from branch (excludes system/developer messages)."""
        if not branch_id:
            return []

        branch_messages = await self._get_branch_messages_from_db(branch_id, conn=conn)
        if not branch_messages:
            return []

        return await self._build_conversation_history(
            branch_messages, with_ids=with_ids, conn=conn
        )

    async def _build_conversation_history(
        self,
        branch_messages: List[MessageResponse],
        *,
        with_ids: bool = False,
        conn: Optional[asyncpg.Connection] = None,
    ) -> List[Union[OpenAIMessageItem, Tuple[str, OpenAIMessageItem]]]:
        """Convert DB messages to OpenAI format, skipping system/developer messages."""
        conversation_history: List[
            Union[OpenAIMessageItem, Tuple[str, OpenAIMessageItem]]
        ] = []

        # Call IDs that have a matching function_call_output in this branch (API requires every function_call to be followed by its output)
        call_ids_with_output: set = set()
        for m in branch_messages:
            if getattr(m, "type", None) == "function_call_output" and getattr(
                m, "call_id", None
            ):
                call_ids_with_output.add(m.call_id)

        # Batch collect all image URLs from all messages for efficient querying
        all_image_urls = ImageProcessor.collect_image_urls_from_messages(
            branch_messages
        )

        # Batch query media_assets for all images (single query for entire conversation)
        expiration_map: Dict[str, Optional[int]] = {}
        media_id_map: Dict[str, Optional[str]] = {}
        if conn and all_image_urls:
            (
                expiration_map,
                media_id_map,
            ) = await ImageProcessor.batch_query_media_assets(conn, all_image_urls)

        # Process each message with shared expiration map and media_id map
        for msg in branch_messages:
            if msg.is_hidden:
                continue

            # Skip function_call messages that have no matching function_call_output (e.g. sql_query before ask_user_question when agent went to waiting_for_user)
            if getattr(msg, "type", None) == "function_call" and getattr(
                msg, "call_id", None
            ):
                if msg.call_id not in call_ids_with_output:
                    continue

            normalized = await self.message_converter.convert_message(
                msg,
                with_ids=with_ids,
                expiration_map=expiration_map,
                media_id_map=media_id_map,
            )
            if not normalized:
                continue

            # Skip any legacy system/developer messages from DB
            role = normalized[1].get("role") if with_ids else normalized.get("role")
            if role in {"system", "developer"}:
                continue

            conversation_history.append(normalized)

        return conversation_history

    async def _get_branch_messages_from_db(
        self, branch_id: str, conn: Optional[asyncpg.Connection] = None
    ) -> Optional[List[MessageResponse]]:
        try:
            # Use ASC order for agent context (oldest first, chronological)
            if conn is None:
                async with get_async_connection() as db_conn:
                    messages_data = await get_all_branch_messages(
                        db_conn, branch_id, order="ASC"
                    )
            else:
                messages_data = await get_all_branch_messages(
                    conn, branch_id, order="ASC"
                )

            if not messages_data:
                return None

            messages: List[MessageResponse] = []
            for msg_data in messages_data:
                try:
                    if msg_data.get("is_hidden", False):
                        continue

                    message = MessageResponse(
                        id=str(msg_data["id"]),
                        conversation_id=str(msg_data["conversation_id"]),
                        sequence_number=msg_data.get("sequence_number", 0),
                        role=msg_data.get("role", "user"),
                        type=msg_data.get("type", "message"),
                        content=msg_data.get("content"),
                        reasoning_summary=msg_data.get("reasoning_summary"),
                        encrypted_content=msg_data.get("encrypted_content"),
                        call_id=msg_data.get("call_id"),
                        function_name=msg_data.get("function_name"),
                        function_arguments=msg_data.get("function_arguments"),
                        function_output=msg_data.get("function_output"),
                        web_search_action=msg_data.get("web_search_action"),
                        status=msg_data.get("status"),
                        metadata=msg_data.get("metadata"),
                        created_at=msg_data.get("created_at", 0),
                        updated_at=msg_data.get("updated_at", 0),
                        is_modified=msg_data.get("is_modified", False),
                        modified_content=msg_data.get("modified_content"),
                        modified_reasoning_summary=msg_data.get(
                            "modified_reasoning_summary"
                        ),
                        modified_function_arguments=msg_data.get(
                            "modified_function_arguments"
                        ),
                        modified_function_output=msg_data.get(
                            "modified_function_output"
                        ),
                        is_hidden=msg_data.get("is_hidden", False),
                    )
                    messages.append(message)
                except Exception as exc:
                    logger.warning(
                        "Error converting message %s to MessageResponse: %s",
                        msg_data.get("id"),
                        exc,
                    )
                    continue

            return messages if messages else None
        except Exception as exc:
            logger.error("Error getting branch messages from DB: %s", exc)
        return None


__all__ = ["ContextBuilder", "OpenAIMessageItem"]
