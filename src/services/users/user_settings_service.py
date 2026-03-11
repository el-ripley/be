"""
Service for managing user conversation settings.

Handles user-level defaults for context management settings.
"""

from typing import Any, Dict, Optional

from src.agent.common.conversation_settings import (
    DEFAULT_CONTEXT_BUFFER_PERCENT,
    DEFAULT_CONTEXT_TOKEN_LIMIT,
    DEFAULT_SUMMARIZER_MODEL,
    DEFAULT_VISION_MODEL,
    SUPPORTED_MODELS,
)
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.user_queries import (
    get_user_conversation_settings,
    upsert_user_conversation_settings,
)
from src.utils.logger import get_logger

logger = get_logger()


class UserSettingsService:
    """Service for managing user conversation settings."""

    async def get_settings(self, user_id: str) -> Dict[str, Any]:
        """
        Get user conversation settings.
        Returns defaults if no settings exist.
        """
        try:
            async with async_db_transaction() as conn:
                record = await get_user_conversation_settings(conn, user_id)

            if not record:
                # Return defaults
                return {
                    "context_token_limit": DEFAULT_CONTEXT_TOKEN_LIMIT,
                    "context_buffer_percent": DEFAULT_CONTEXT_BUFFER_PERCENT,
                    "summarizer_model": DEFAULT_SUMMARIZER_MODEL,
                    "vision_model": DEFAULT_VISION_MODEL,
                }

            # Return settings with defaults for None values
            return {
                "context_token_limit": record.get("context_token_limit")
                or DEFAULT_CONTEXT_TOKEN_LIMIT,
                "context_buffer_percent": record.get("context_buffer_percent")
                or DEFAULT_CONTEXT_BUFFER_PERCENT,
                "summarizer_model": record.get("summarizer_model")
                or DEFAULT_SUMMARIZER_MODEL,
                "vision_model": record.get("vision_model") or DEFAULT_VISION_MODEL,
            }

        except Exception as e:
            logger.error(
                f"Error getting user conversation settings for {user_id}: {str(e)}"
            )
            raise

    async def update_settings(
        self,
        user_id: str,
        context_token_limit: Optional[int] = None,
        context_buffer_percent: Optional[int] = None,
        summarizer_model: Optional[str] = None,
        vision_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update user conversation settings.

        Args:
            user_id: User ID
            context_token_limit: Optional context token limit (> 0)
            context_buffer_percent: Optional buffer percentage (0-100)
            summarizer_model: Optional summarizer model (must be in SUPPORTED_MODELS)
            vision_model: Optional vision model (must be in SUPPORTED_MODELS)

        Returns:
            Updated settings record
        """
        # Validate inputs
        if context_token_limit is not None and context_token_limit <= 0:
            raise ValueError("context_token_limit must be greater than 0")

        if context_buffer_percent is not None and (
            context_buffer_percent < 0 or context_buffer_percent > 100
        ):
            raise ValueError("context_buffer_percent must be between 0 and 100")

        if summarizer_model is not None and summarizer_model not in SUPPORTED_MODELS:
            raise ValueError(
                f"Invalid summarizer_model: {summarizer_model}. "
                f"Supported models: {', '.join(SUPPORTED_MODELS)}"
            )

        if vision_model is not None and vision_model not in SUPPORTED_MODELS:
            raise ValueError(
                f"Invalid vision_model: {vision_model}. "
                f"Supported models: {', '.join(SUPPORTED_MODELS)}"
            )

        try:
            async with async_db_transaction() as conn:
                result = await upsert_user_conversation_settings(
                    conn=conn,
                    user_id=user_id,
                    context_token_limit=context_token_limit,
                    context_buffer_percent=context_buffer_percent,
                    summarizer_model=summarizer_model,
                    vision_model=vision_model,
                )

            # Return formatted result with defaults for None values
            return {
                "context_token_limit": result.get("context_token_limit")
                or DEFAULT_CONTEXT_TOKEN_LIMIT,
                "context_buffer_percent": result.get("context_buffer_percent")
                or DEFAULT_CONTEXT_BUFFER_PERCENT,
                "summarizer_model": result.get("summarizer_model")
                or DEFAULT_SUMMARIZER_MODEL,
                "vision_model": result.get("vision_model") or DEFAULT_VISION_MODEL,
            }

        except ValueError:
            raise
        except Exception as e:
            logger.error(
                f"Error updating user conversation settings for {user_id}: {str(e)}"
            )
            raise
