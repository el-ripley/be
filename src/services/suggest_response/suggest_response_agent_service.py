"""
Suggest Response Agent Service.

Handles business logic for suggest response agent settings.
Reuses conversation_settings.py for settings validation and normalization.
"""

import json
from typing import Any, Dict, Optional

from src.agent.common.conversation_settings import (
    get_default_settings,
    normalize_settings,
    validate_settings,
)
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories import get_agent_settings, upsert_agent_settings
from src.utils.logger import get_logger

logger = get_logger()


class SuggestResponseAgentService:
    """Service for managing suggest response agent settings."""

    @staticmethod
    def _format_agent_record(
        record: Optional[Dict[str, Any]], user_id: str
    ) -> Dict[str, Any]:
        """Normalize DB record into API-friendly dict."""
        if not record:
            result = {
                "user_id": user_id,
                "settings": get_default_settings(),
                "allow_auto_suggest": False,
                "num_suggest_response": 3,
            }
            return result

        data = dict(record)

        # Parse JSONB settings if it's a string
        settings = data.get("settings")
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse settings JSON: {settings}")
                settings = get_default_settings()
        elif settings is None:
            settings = get_default_settings()
        else:
            # Ensure it's a dict
            settings = dict(settings) if settings else get_default_settings()

        # Normalize settings with defaults
        try:
            settings = normalize_settings(settings)
        except ValueError as e:
            logger.warning(f"Invalid settings in DB, using defaults: {e}")
            settings = get_default_settings()

        data["settings"] = settings

        # Ensure defaults for other fields
        if "allow_auto_suggest" not in data:
            data["allow_auto_suggest"] = False
        if "num_suggest_response" not in data:
            data["num_suggest_response"] = 3

        # Ensure user_id is always present (use provided user_id if record doesn't have it)
        if "user_id" not in data or data["user_id"] is None:
            data["user_id"] = user_id

        # Convert UUID to string if needed
        if "id" in data and data["id"] is not None:
            data["id"] = str(data["id"])

        return data

    async def get_settings(self, user_id: str) -> Dict[str, Any]:
        """
        Get suggest response agent settings for a user.
        Returns defaults if no settings exist (lazy creation pattern).
        """
        try:
            async with async_db_transaction() as conn:
                record = await get_agent_settings(conn, user_id)
                result = self._format_agent_record(record, user_id)
                logger.info(f"Retrieved suggest response settings for user {user_id}")
                return result

        except Exception as e:
            logger.error(
                f"Error getting suggest response settings for user {user_id}: {str(e)}"
            )
            raise

    async def update_settings(
        self,
        user_id: str,
        settings: Optional[Dict[str, Any]] = None,
        allow_auto_suggest: Optional[bool] = None,
        num_suggest_response: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Update suggest response agent settings.
        Uses upsert pattern (lazy creation).
        """
        try:
            # Get current settings or defaults
            current = await self.get_settings(user_id)

            # Merge settings if provided
            if settings is not None:
                # Validate settings first
                is_valid, error_msg = validate_settings(settings)
                if not is_valid:
                    raise ValueError(f"Invalid settings: {error_msg}")

                # Normalize settings (merge with defaults)
                normalized_settings = normalize_settings(settings)
            else:
                normalized_settings = current["settings"]

            # Use provided values or keep current
            final_allow_auto_suggest = (
                allow_auto_suggest
                if allow_auto_suggest is not None
                else current["allow_auto_suggest"]
            )
            final_num_suggest_response = (
                num_suggest_response
                if num_suggest_response is not None
                else current["num_suggest_response"]
            )

            # Validate num_suggest_response
            if final_num_suggest_response < 1:
                raise ValueError("num_suggest_response must be at least 1")
            if final_num_suggest_response > 10:
                raise ValueError("num_suggest_response cannot exceed 10")

            # Upsert to database
            async with async_db_transaction() as conn:
                result = await upsert_agent_settings(
                    conn,
                    user_id,
                    normalized_settings,
                    final_allow_auto_suggest,
                    final_num_suggest_response,
                )

                logger.info(f"Updated suggest response settings for user {user_id}")
                return self._format_agent_record(result, user_id)

        except ValueError:
            raise
        except Exception as e:
            logger.error(
                f"Error updating suggest response settings for user {user_id}: {str(e)}"
            )
            raise
