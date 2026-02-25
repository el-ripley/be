"""Dynamic system prompt builder with live data injection."""

from datetime import datetime, timezone
from typing import Optional

import asyncpg

from src.agent.general_agent.context.messages.system_prompt import (
    build_base_system_prompt,
)


class SystemPromptBuilder:
    """Builds dynamic system prompts with live context data."""

    def __init__(self):
        pass

    async def build_prompt(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        model_name: Optional[str] = None,
    ) -> str:
        """
        Build system prompt with live data injected via template variables.

        Args:
            conn: Database connection
            user_id: User ID
            model_name: Model name (e.g., "gpt-5.2")

        Returns:
            Complete system prompt with injected live data
        """
        # Fetch live data
        current_time = self._get_current_timestamp()

        # Fetch and render user memory (global user-level memory)
        user_memory_content, user_memory_prompt_id = await self._fetch_user_memory(
            conn, user_id
        )

        # Build prompt with variables injected
        return build_base_system_prompt(
            current_time=current_time,
            model_name=model_name,
            user_memory=user_memory_content,
            user_memory_prompt_id=user_memory_prompt_id,
        )

    async def _fetch_user_memory(
        self, conn: asyncpg.Connection, user_id: str
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Fetch and render user memory (global user-level memory) with media.

        Returns:
            Tuple of (rendered_content, prompt_id). If no container exists, returns
            (None, None). On error, (None, None).
        """
        try:
            from src.database.postgres.repositories.suggest_response_queries import (
                get_active_user_memory,
            )
            from src.services.suggest_response.memory_blocks_service import (
                MemoryBlocksService,
            )

            user_memory = await get_active_user_memory(conn, user_id)
            if not user_memory:
                return None, None

            memory_blocks_service = MemoryBlocksService()
            prompt_id = str(user_memory["id"])
            raw_rendered = await memory_blocks_service.render_memory(
                memory_type="user_memory", prompt_id=prompt_id
            )
            content = raw_rendered if raw_rendered and raw_rendered.strip() else None
            return content, prompt_id
        except Exception:
            return None, None

    def _get_current_timestamp(self) -> str:
        """Get current timestamp in ISO format with UTC timezone."""
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["SystemPromptBuilder"]
