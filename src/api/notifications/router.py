"""Notifications API router."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from src.middleware.auth_middleware import get_current_user_id
from .handler import NotificationHandler
from .schemas import (
    NotificationItem,
    NotificationListResponse,
    UnreadCountResponse,
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def get_notification_handler(request: Request) -> NotificationHandler:
    """Get NotificationHandler from app state."""
    return NotificationHandler(
        notification_service=request.app.state.notification_service,
    )


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    is_read: Optional[bool] = None,
    limit: int = 20,
    offset: int = 0,
    user_id: str = Depends(get_current_user_id),
    handler: NotificationHandler = Depends(get_notification_handler),
):
    """List notifications for the current user (paginated)."""
    try:
        result = await handler.get_notifications(
            user_id=user_id,
            is_read=is_read,
            limit=limit,
            offset=offset,
        )
        items = [NotificationItem(**item) for item in result["items"]]
        return NotificationListResponse(
            items=items,
            total_unread=result["total_unread"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    user_id: str = Depends(get_current_user_id),
    handler: NotificationHandler = Depends(get_notification_handler),
):
    """Get unread notification count (e.g. for badge)."""
    try:
        count = await handler.get_unread_count(user_id=user_id)
        return UnreadCountResponse(unread_count=count)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/read-all")
async def mark_all_read(
    user_id: str = Depends(get_current_user_id),
    handler: NotificationHandler = Depends(get_notification_handler),
):
    """Mark all notifications as read."""
    try:
        await handler.mark_all_read(user_id=user_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{notification_id}/read", response_model=NotificationItem)
async def mark_notification_read(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
    handler: NotificationHandler = Depends(get_notification_handler),
):
    """Mark a single notification as read."""
    try:
        updated = await handler.mark_read(
            user_id=user_id,
            notification_id=notification_id,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Notification not found")
        return NotificationItem(**updated)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
