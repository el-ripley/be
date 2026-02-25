"""Schemas for notifications API."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class NotificationItem(BaseModel):
    """Single notification in list."""

    id: str
    owner_user_id: str
    type: str
    title: str
    body: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    is_read: bool
    read_at: Optional[int] = None
    created_at: int

    class Config:
        from_attributes = True


class NotificationListResponse(BaseModel):
    """Response for GET /notifications."""

    items: List[NotificationItem]
    total_unread: int


class UnreadCountResponse(BaseModel):
    """Response for GET /notifications/unread-count."""

    unread_count: int
