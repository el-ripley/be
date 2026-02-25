"""
Agent block service: business logic for conversation_agent_blocks.
Verifies user is admin of fan_page before get/upsert.
"""

from typing import Any, Dict, Optional

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories import get_active_block, upsert_block
from src.utils.logger import get_logger

logger = get_logger()


class AgentBlockService:
    """Service for managing conversation agent blocks."""

    def __init__(self, permission_service: Any):
        """
        Args:
            permission_service: FacebookPermissionService for page admin checks
        """
        self.permission_service = permission_service

    async def get_block_status(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get block status for a conversation. Verifies user is admin of fan_page.

        Returns:
            Block record with id, is_active, blocked_by, reason, created_at; or None if not blocked
        """
        if not await self.permission_service.check_user_page_admin_permission(
            user_id, fan_page_id
        ):
            raise PermissionError(f"User {user_id} is not admin of page {fan_page_id}")

        async with async_db_transaction() as conn:
            block = await get_active_block(
                conn, conversation_type, conversation_id, fan_page_id
            )
        return block

    async def upsert_block(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        is_active: bool,
        reason: Optional[str] = None,
        blocked_by: str = "user",
    ) -> Dict[str, Any]:
        """
        Create or update block for a conversation. Verifies user is admin of fan_page.

        Args:
            blocked_by: 'user' when called from API; agent uses 'suggest_response_agent' or 'general_agent'
        """
        if not await self.permission_service.check_user_page_admin_permission(
            user_id, fan_page_id
        ):
            raise PermissionError(f"User {user_id} is not admin of page {fan_page_id}")

        async with async_db_transaction() as conn:
            result = await upsert_block(
                conn,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                fan_page_id=fan_page_id,
                blocked_by=blocked_by,
                reason=reason,
                is_active=is_active,
            )
        return result
