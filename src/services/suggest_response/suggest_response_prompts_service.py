"""
Suggest Response Prompts Service.

Handles business logic for page-level and page-scope user-level prompts.
"""

from typing import Any, Dict, Optional

from src.database.postgres.connection import async_db_transaction
from src.utils.logger import get_logger

logger = get_logger()


class SuggestResponsePromptsService:
    """Service for managing suggest response prompts."""

    @staticmethod
    def _format_prompt_record(
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

        # Parse media field from JSONB (could be string, list, or None)
        if "media" in data:
            media_value = data.get("media")
            if media_value is None:
                data["media"] = []
            elif isinstance(media_value, str):
                # Parse JSON string
                try:
                    parsed = json.loads(media_value)
                    data["media"] = parsed if isinstance(parsed, list) else []
                except (json.JSONDecodeError, TypeError):
                    data["media"] = []
            elif isinstance(media_value, list):
                # Already a list, keep as is
                data["media"] = media_value
            else:
                # Unknown type, default to empty list
                data["media"] = []

        return data

    # ================================================================
    # PAGE PROMPTS
    # ================================================================

    async def get_page_prompt(
        self,
        fan_page_id: str,
        prompt_type: str,
        owner_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get active page prompt for a specific page and prompt type.

        Args:
            fan_page_id: Facebook page ID
            prompt_type: 'messages' or 'comments'
            owner_user_id: User who owns this page in the app
        """
        try:
            # Validate prompt_type
            if prompt_type not in ["messages", "comments"]:
                raise ValueError(
                    f"Invalid prompt_type: {prompt_type}. Must be 'messages' or 'comments'"
                )

            async with async_db_transaction() as conn:
                from src.database.postgres.repositories.suggest_response_queries import (
                    get_active_page_prompt_with_media,
                )

                record = await get_active_page_prompt_with_media(
                    conn, fan_page_id, prompt_type, owner_user_id
                )
                result = self._format_prompt_record(record)
                logger.info(
                    f"Retrieved page prompt for page {fan_page_id}, type {prompt_type}, owner {owner_user_id}"
                )
                return result

        except ValueError:
            raise
        except Exception as e:
            logger.error(
                f"Error getting page prompt for page {fan_page_id}, type {prompt_type}: {str(e)}"
            )
            raise

    # ================================================================
    # PAGE SCOPE USER PROMPTS
    # ================================================================

    async def get_page_scope_user_prompt(
        self,
        fan_page_id: str,
        facebook_page_scope_user_id: str,
        owner_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get active page-scope user prompt for a specific user on a page.
        Only applicable for messages (not comments).

        Args:
            fan_page_id: Facebook page ID
            facebook_page_scope_user_id: Page-scoped user ID (PSID)
            owner_user_id: User who owns this page in the app
        """
        try:
            async with async_db_transaction() as conn:
                from src.database.postgres.repositories.suggest_response_queries import (
                    get_active_page_scope_user_prompt_with_media,
                )

                record = await get_active_page_scope_user_prompt_with_media(
                    conn, fan_page_id, facebook_page_scope_user_id, owner_user_id
                )
                result = self._format_prompt_record(record)
                logger.info(
                    f"Retrieved page-scope user prompt for page {fan_page_id}, user {facebook_page_scope_user_id}, owner {owner_user_id}"
                )
                return result

        except Exception as e:
            logger.error(
                f"Error getting page-scope user prompt for page {fan_page_id}, user {facebook_page_scope_user_id}: {str(e)}"
            )
            raise
