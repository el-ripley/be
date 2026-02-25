"""Handler for notifications API."""

from typing import Any, Dict, Optional

from src.services.notifications import NotificationService
from src.utils.logger import get_logger

logger = get_logger()


class NotificationHandler:
    """Handler for notification list, unread count, and mark-read endpoints."""

    def __init__(self, notification_service: NotificationService):
        self.notification_service = notification_service

    async def get_notifications(
        self,
        user_id: str,
        is_read: Optional[bool] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List notifications with optional filter and pagination."""
        return await self.notification_service.get_notifications(
            owner_user_id=user_id,
            is_read=is_read,
            limit=limit,
            offset=offset,
        )

    async def get_unread_count(self, user_id: str) -> int:
        """Get unread notification count."""
        return await self.notification_service.get_unread_count(
            owner_user_id=user_id,
        )

    async def mark_read(
        self, user_id: str, notification_id: str
    ) -> Optional[Dict[str, Any]]:
        """Mark a single notification as read."""
        return await self.notification_service.mark_read(
            notification_id=notification_id,
            owner_user_id=user_id,
        )

    async def mark_all_read(self, user_id: str) -> None:
        """Mark all notifications as read."""
        await self.notification_service.mark_all_read(owner_user_id=user_id)
