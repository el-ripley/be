"""
User memory service: business logic for global user-level memory (user_memory + memory_blocks).
Read-only for users: get and soft-delete only.
"""

from typing import Any, Dict, Optional

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories import (
    deactivate_user_memory,
    get_active_user_memory_with_blocks,
)
from src.utils.logger import get_logger

logger = get_logger()


class UserMemoryService:
    """Service for user-facing user_memory operations (view and delete)."""

    async def get_user_memory(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get active user memory with its memory_blocks for the given user."""
        async with async_db_transaction() as conn:
            result = await get_active_user_memory_with_blocks(conn, user_id)
        return result

    async def delete_user_memory(self, user_id: str) -> bool:
        """Soft delete active user memory (set is_active=FALSE). Returns True if any was deactivated."""
        async with async_db_transaction() as conn:
            updated = await deactivate_user_memory(conn, user_id)
        return updated
