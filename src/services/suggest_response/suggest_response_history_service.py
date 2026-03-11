"""
Suggest Response History Service.

Handles business logic for suggest response history records.
"""

from typing import Any, Dict, List, Optional

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories import (
    count_suggest_response_history_by_conversation,
    count_suggest_response_history_by_page,
    count_suggest_response_history_with_filters,
    get_suggest_response_history_by_conversation,
    get_suggest_response_history_by_id,
    get_suggest_response_history_by_page,
    get_suggest_response_history_with_filters,
    get_suggest_response_messages_by_history,
    update_suggest_response_history,
)
from src.utils.logger import get_logger

logger = get_logger()


class SuggestResponseHistoryService:
    """Service for managing suggest response history."""

    @staticmethod
    def _format_history_record(
        record: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Normalize DB record into API-friendly dict."""
        if not record:
            return None

        import json

        data = dict(record)

        # Convert UUID to string if needed
        if "id" in data and data["id"] is not None:
            data["id"] = str(data["id"])

        # Parse suggestions field from JSONB
        if "suggestions" in data:
            suggestions_value = data.get("suggestions")
            if suggestions_value is None:
                data["suggestions"] = []
            elif isinstance(suggestions_value, str):
                # Parse JSON string
                try:
                    parsed = json.loads(suggestions_value)
                    data["suggestions"] = parsed if isinstance(parsed, list) else []
                except (json.JSONDecodeError, TypeError):
                    data["suggestions"] = []
            elif isinstance(suggestions_value, list):
                # Already a list, keep as is
                data["suggestions"] = suggestions_value
            else:
                data["suggestions"] = []

        # Convert UUID fields to strings
        for field in [
            "user_id",
            "facebook_conversation_comments_id",
            "page_prompt_id",
            "page_scope_user_prompt_id",
            "agent_response_id",
        ]:
            if field in data and data[field] is not None:
                data[field] = str(data[field])

        return data

    async def get_history_by_id(self, history_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a suggest response history record by ID.

        Args:
            history_id: History record UUID

        Returns:
            Dict with history record or None if not found
        """
        try:
            async with async_db_transaction() as conn:
                record = await get_suggest_response_history_by_id(conn, history_id)
                result = self._format_history_record(record)
                if result:
                    logger.info(f"Retrieved suggest response history {history_id}")
                return result

        except Exception as e:
            logger.error(
                f"Error getting suggest response history {history_id}: {str(e)}"
            )
            raise

    async def get_history_by_conversation(
        self,
        conversation_type: str,
        conversation_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get suggest response history records for a specific conversation.

        Args:
            conversation_type: 'messages' or 'comments'
            conversation_id: Conversation ID (UUID for messages, UUID or Facebook ID for comments)
            limit: Maximum number of records to return (1-100)
            offset: Number of records to skip

        Returns:
            Dict with 'history' list and 'total' count
        """
        try:
            # Validate conversation_type
            if conversation_type not in ["messages", "comments"]:
                raise ValueError(
                    f"Invalid conversation_type: {conversation_type}. Must be 'messages' or 'comments'"
                )

            # Validate limit
            if limit < 1 or limit > 100:
                raise ValueError("limit must be between 1 and 100")

            if offset < 0:
                raise ValueError("offset must be >= 0")

            async with async_db_transaction() as conn:
                records = await get_suggest_response_history_by_conversation(
                    conn, conversation_type, conversation_id, limit, offset
                )
                total = await count_suggest_response_history_by_conversation(
                    conn, conversation_type, conversation_id
                )

                history = [self._format_history_record(r) for r in records if r]

                logger.info(
                    f"Retrieved {len(history)} suggest response history records for {conversation_type} {conversation_id}"
                )

                return {"history": history, "total": total}

        except ValueError:
            raise
        except Exception as e:
            logger.error(
                f"Error getting suggest response history for {conversation_type} {conversation_id}: {str(e)}"
            )
            raise

    async def get_history_by_page(
        self,
        fan_page_id: str,
        user_id: str,
        conversation_type: Optional[str] = None,
        trigger_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get suggest response history records for a specific page.

        Args:
            fan_page_id: Facebook page ID
            user_id: User ID
            conversation_type: Optional filter by 'messages' or 'comments'
            trigger_type: Optional filter by 'user' or 'auto'
            limit: Maximum number of records to return (1-100)
            offset: Number of records to skip

        Returns:
            Dict with 'history' list and 'total' count
        """
        try:
            # Validate conversation_type if provided
            if conversation_type and conversation_type not in ["messages", "comments"]:
                raise ValueError(
                    f"Invalid conversation_type: {conversation_type}. Must be 'messages' or 'comments'"
                )

            # Validate trigger_type if provided
            if trigger_type and trigger_type not in [
                "user",
                "auto",
                "webhook_suggest",
                "webhook_auto_reply",
            ]:
                raise ValueError(
                    f"Invalid trigger_type: {trigger_type}. Must be 'user', 'auto', "
                    "'webhook_suggest', or 'webhook_auto_reply'"
                )

            # Validate limit
            if limit < 1 or limit > 100:
                raise ValueError("limit must be between 1 and 100")

            if offset < 0:
                raise ValueError("offset must be >= 0")

            async with async_db_transaction() as conn:
                records = await get_suggest_response_history_by_page(
                    conn,
                    fan_page_id,
                    user_id,
                    conversation_type,
                    trigger_type,
                    limit,
                    offset,
                )
                total = await count_suggest_response_history_by_page(
                    conn, fan_page_id, user_id, conversation_type, trigger_type
                )

                history = [self._format_history_record(r) for r in records if r]

                logger.info(
                    f"Retrieved {len(history)} suggest response history records for page {fan_page_id}"
                )

                return {"history": history, "total": total}

        except ValueError:
            raise
        except Exception as e:
            logger.error(
                f"Error getting suggest response history for page {fan_page_id}: {str(e)}"
            )
            raise

    async def update_history(
        self,
        history_id: str,
        selected_suggestion_index: Optional[int] = None,
        reaction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update suggest response history record with selected_suggestion_index and/or reaction.

        Args:
            history_id: History record UUID
            selected_suggestion_index: Index of selected suggestion (0-based, can be None to clear)
            reaction: 'like' or 'dislike' (can be None to clear)

        Returns:
            Updated history record
        """
        try:
            # Validate selected_suggestion_index if provided
            if selected_suggestion_index is not None and selected_suggestion_index < 0:
                raise ValueError("selected_suggestion_index must be >= 0")

            # Validate reaction if provided
            if reaction and reaction not in ["like", "dislike"]:
                raise ValueError("reaction must be 'like' or 'dislike'")

            async with async_db_transaction() as conn:
                # First check if record exists and get suggestion_count to validate index
                existing = await get_suggest_response_history_by_id(conn, history_id)
                if not existing:
                    raise ValueError(f"History record {history_id} not found")

                # Validate selected_suggestion_index against suggestion_count
                suggestion_count = existing.get("suggestion_count", 0)
                if (
                    selected_suggestion_index is not None
                    and selected_suggestion_index >= suggestion_count
                ):
                    raise ValueError(
                        f"selected_suggestion_index {selected_suggestion_index} is out of range. "
                        f"Maximum index is {suggestion_count - 1}"
                    )

                # Update record
                updated = await update_suggest_response_history(
                    conn,
                    history_id,
                    selected_suggestion_index,
                    reaction,
                )

                if not updated:
                    raise ValueError(f"Failed to update history record {history_id}")

                result = self._format_history_record(updated)
                logger.info(
                    f"Updated suggest response history {history_id}: "
                    f"selected_suggestion_index={selected_suggestion_index}, reaction={reaction}"
                )

                return result

        except ValueError:
            raise
        except Exception as e:
            logger.error(
                f"Error updating suggest response history {history_id}: {str(e)}"
            )
            raise

    async def get_history_with_filters(
        self,
        user_id: str,
        fan_page_id: Optional[str] = None,
        conversation_type: Optional[str] = None,
        facebook_conversation_messages_id: Optional[str] = None,
        facebook_conversation_comments_id: Optional[str] = None,
        page_prompt_id: Optional[str] = None,
        page_scope_user_prompt_id: Optional[str] = None,
        suggestion_count: Optional[int] = None,
        trigger_type: Optional[str] = None,
        reaction: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get suggest response history records with comprehensive filters.

        Args:
            user_id: User ID (required - always filter by user)
            fan_page_id: Optional filter by page
            conversation_type: Optional filter by 'messages' or 'comments'
            facebook_conversation_messages_id: Optional filter by messages conversation ID
            facebook_conversation_comments_id: Optional filter by comments conversation ID
            page_prompt_id: Optional filter by page prompt ID
            page_scope_user_prompt_id: Optional filter by page scope user prompt ID
            suggestion_count: Optional filter by exact suggestion count
            trigger_type: Optional filter by 'user' or 'auto'
            reaction: Optional filter by 'like' or 'dislike'
            limit: Maximum number of records to return (1-100)
            offset: Number of records to skip

        Returns:
            Dict with 'history' list and 'total' count
        """
        try:
            # Validate conversation_type if provided
            if conversation_type and conversation_type not in ["messages", "comments"]:
                raise ValueError(
                    f"Invalid conversation_type: {conversation_type}. Must be 'messages' or 'comments'"
                )

            # Validate trigger_type if provided
            if trigger_type and trigger_type not in [
                "user",
                "auto",
                "webhook_suggest",
                "webhook_auto_reply",
            ]:
                raise ValueError(
                    f"Invalid trigger_type: {trigger_type}. Must be 'user', 'auto', "
                    "'webhook_suggest', or 'webhook_auto_reply'"
                )

            # Validate reaction if provided
            if reaction and reaction not in ["like", "dislike"]:
                raise ValueError(
                    f"Invalid reaction: {reaction}. Must be 'like' or 'dislike'"
                )

            # Validate suggestion_count if provided
            if suggestion_count is not None and suggestion_count < 0:
                raise ValueError("suggestion_count must be >= 0")

            # Validate limit
            if limit < 1 or limit > 100:
                raise ValueError("limit must be between 1 and 100")

            if offset < 0:
                raise ValueError("offset must be >= 0")

            async with async_db_transaction() as conn:
                records = await get_suggest_response_history_with_filters(
                    conn,
                    user_id=user_id,
                    fan_page_id=fan_page_id,
                    conversation_type=conversation_type,
                    facebook_conversation_messages_id=facebook_conversation_messages_id,
                    facebook_conversation_comments_id=facebook_conversation_comments_id,
                    page_prompt_id=page_prompt_id,
                    page_scope_user_prompt_id=page_scope_user_prompt_id,
                    suggestion_count=suggestion_count,
                    trigger_type=trigger_type,
                    reaction=reaction,
                    limit=limit,
                    offset=offset,
                )
                total = await count_suggest_response_history_with_filters(
                    conn,
                    user_id=user_id,
                    fan_page_id=fan_page_id,
                    conversation_type=conversation_type,
                    facebook_conversation_messages_id=facebook_conversation_messages_id,
                    facebook_conversation_comments_id=facebook_conversation_comments_id,
                    page_prompt_id=page_prompt_id,
                    page_scope_user_prompt_id=page_scope_user_prompt_id,
                    suggestion_count=suggestion_count,
                    trigger_type=trigger_type,
                    reaction=reaction,
                )

                history = [self._format_history_record(r) for r in records if r]

                logger.info(
                    f"Retrieved {len(history)} suggest response history records with filters for user {user_id}"
                )

                return {"history": history, "total": total}

        except ValueError:
            raise
        except Exception as e:
            logger.error(
                f"Error getting suggest response history with filters for user {user_id}: {str(e)}"
            )
            raise

    @staticmethod
    def _format_message_record(
        record: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Pass-through; repository already returns normalized rows (id/history_id str, JSONB as dict)."""
        return dict(record) if record else None

    async def get_messages_by_history(
        self, history_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get message items for a suggest response history record.

        Args:
            history_id: History record UUID
            user_id: User ID (for permission check - history must belong to this user)

        Returns:
            Dict with 'messages' list, or None if history not found or user mismatch
        """
        try:
            async with async_db_transaction() as conn:
                history = await get_suggest_response_history_by_id(conn, history_id)
                if not history:
                    return None
                if str(history.get("user_id")) != str(user_id):
                    return None
                records = await get_suggest_response_messages_by_history(
                    conn, history_id
                )
                messages = [self._format_message_record(r) for r in records if r]
                logger.info(
                    f"Retrieved {len(messages)} message items for history {history_id}"
                )
                return {"messages": messages}
        except Exception as e:
            logger.error(
                f"Error getting suggest response messages for history {history_id}: {str(e)}"
            )
            raise
