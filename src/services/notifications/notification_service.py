"""
Generic notification service: persistence + real-time socket delivery.
No knowledge of escalations or other domains; callers provide type/title/body/reference.
"""

from typing import Any, Dict, Optional

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories import (
    count_unread_notifications,
    get_notifications,
    insert_notification,
    mark_all_notifications_read,
    mark_notification_read,
)
from src.socket_service import SocketService
from src.utils.logger import get_logger
from src.utils.serialization import to_serializable

logger = get_logger()


class NotificationService:
    """Generic service for in-app notifications: CRUD + socket emit."""

    def __init__(self, socket_service: SocketService):
        self.socket_service = socket_service

    async def create(
        self,
        owner_user_id: str,
        type: str,
        title: str,
        body: Optional[str] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a notification, persist it, and emit notification.new to the user.
        Returns the created notification dict.
        """
        async with async_db_transaction() as conn:
            row = await insert_notification(
                conn,
                owner_user_id=owner_user_id,
                type=type,
                title=title,
                body=body,
                reference_type=reference_type,
                reference_id=reference_id,
                metadata=metadata,
            )
        payload = to_serializable(row)
        await self.socket_service.emit_notification(owner_user_id, payload)
        return row

    async def get_notifications(
        self,
        owner_user_id: str,
        is_read: Optional[bool] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        List notifications for the user (paginated, newest first).
        Returns dict with 'items' (list) and 'total_unread' (int).
        """
        async with async_db_transaction() as conn:
            items = await get_notifications(
                conn,
                owner_user_id=owner_user_id,
                is_read=is_read,
                limit=limit,
                offset=offset,
            )
            total_unread = await count_unread_notifications(conn, owner_user_id)
        return {"items": items, "total_unread": total_unread}

    async def get_unread_count(self, owner_user_id: str) -> int:
        """Return the number of unread notifications for the user."""
        async with async_db_transaction() as conn:
            return await count_unread_notifications(conn, owner_user_id)

    async def mark_read(
        self, notification_id: str, owner_user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Mark a single notification as read. Returns updated row or None if not found."""
        async with async_db_transaction() as conn:
            return await mark_notification_read(
                conn, notification_id=notification_id, owner_user_id=owner_user_id
            )

    async def mark_all_read(self, owner_user_id: str) -> None:
        """Mark all notifications for the user as read."""
        async with async_db_transaction() as conn:
            await mark_all_notifications_read(conn, owner_user_id=owner_user_id)
