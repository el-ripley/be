"""Pydantic schemas for Facebook sync API."""

from typing import Optional, Literal
from pydantic import BaseModel, Field


# ============================================================================
# POSTS SYNC
# ============================================================================


class PostsSyncRequest(BaseModel):
    """Request payload for syncing posts."""

    page_id: str = Field(..., description="Facebook Page ID to sync posts from")
    limit: int = Field(
        25, ge=1, le=100, description="Max posts to sync in this batch (1-100)"
    )
    continue_from_cursor: bool = Field(
        True, description="Continue from saved cursor (True) or restart (False)"
    )


class PostsSyncResult(BaseModel):
    """Response for posts sync operation."""

    fan_page_id: str
    synced_posts: int  # Only new posts (INSERT)
    updated_posts: Optional[int] = 0  # Posts that were updated (UPDATE)
    has_more: bool
    cursor: Optional[str] = None
    status: Literal["idle", "in_progress", "completed", "error"]
    cursor_was_reset: Optional[bool] = None
    error: Optional[str] = None


class PostsSyncStatus(BaseModel):
    """Posts sync status for a page."""

    status: Literal["idle", "in_progress", "completed", "error"]
    posts_cursor: Optional[str] = None
    total_synced_posts: int
    last_sync_at: Optional[int] = None


class SyncStatusResponse(BaseModel):
    """Sync status for a page."""

    fan_page_id: str
    posts_sync: PostsSyncStatus


# ============================================================================
# COMMENTS SYNC
# ============================================================================


class CommentsSyncRequest(BaseModel):
    """Request payload for syncing comments of a post."""

    page_id: str = Field(..., description="Facebook Page ID")
    post_id: str = Field(..., description="Post ID to sync comments for")
    limit: int = Field(
        10, ge=1, le=50, description="Max ROOT comment trees per batch (1-50)"
    )
    continue_from_cursor: bool = Field(
        True, description="Continue from saved cursor (True) or restart (False)"
    )


class CommentsSyncResult(BaseModel):
    """Response for comments sync operation."""

    fan_page_id: str
    post_id: str
    synced_root_comments: int
    synced_total_comments: int
    has_more: bool
    cursor: Optional[str] = None
    status: Literal["idle", "in_progress", "completed", "error"]
    cursor_was_reset: Optional[bool] = None
    error: Optional[str] = None


class CommentSyncStatusResponse(BaseModel):
    """Comment sync status for a specific post."""

    post_id: str
    status: Literal["idle", "in_progress", "completed", "error"]
    comments_cursor: Optional[str] = None
    total_synced_root_comments: int
    total_synced_comments: int
    last_sync_at: Optional[int] = None


# ============================================================================
# MESSAGES SYNC
# ============================================================================


class InboxSyncRequest(BaseModel):
    """Request payload for triggering an inbox sync batch."""

    page_id: str = Field(..., description="Facebook Page ID to sync")
    limit: int = Field(
        50,
        ge=1,
        le=100,
        description="Max conversations to sync in this batch (1-100)",
    )
    messages_per_conv: int = Field(
        100,
        ge=1,
        le=500,
        description="Max messages to sync per conversation (1-500)",
    )
    continue_from_cursor: bool = Field(
        True,
        description="Whether to continue from last saved cursor (True) or restart (False)",
    )


class InboxSyncResult(BaseModel):
    """Response payload for a single inbox sync batch."""

    fan_page_id: str
    synced_conversations: int
    synced_messages: int
    skipped_conversations: int
    has_more: bool
    cursor: Optional[str] = None
    status: Literal["idle", "in_progress", "completed", "error"]
    cursor_was_reset: Optional[bool] = None
    error: Optional[str] = None


class InboxSyncStatusResponse(BaseModel):
    """Current inbox sync status for a page."""

    fan_page_id: str
    status: Literal["idle", "in_progress", "completed", "error"]
    fb_cursor: Optional[str] = None
    total_synced_conversations: int
    total_synced_messages: int
    last_sync_at: Optional[int] = None


# ============================================================================
# FULL SYNC
# ============================================================================


class FullSyncRequest(BaseModel):
    """Request payload for full sync."""

    page_id: str = Field(..., description="Facebook Page ID")
    posts_limit: int = Field(
        50, ge=1, le=200, description="Max posts to sync comments for (1-200)"
    )
    comments_per_post: int = Field(
        20, ge=1, le=50, description="Max root comments per post (1-50)"
    )


class FullSyncResult(BaseModel):
    """Response for full sync operation."""

    fan_page_id: str
    status: Literal["completed", "error"]
    total_posts_synced: int
    total_comments_synced: int
    posts_with_comments: Optional[int] = None
    total_root_comments: Optional[int] = None
    error: Optional[str] = None


class CommentsSyncStatus(BaseModel):
    """Comments sync status."""

    status: Literal["completed", "pending"]
    has_posts_needing_sync: bool


class FullSyncStatusResponse(BaseModel):
    """Full sync status response."""

    fan_page_id: str
    posts_sync: PostsSyncStatus
    comments_sync: CommentsSyncStatus
    overall_status: Literal["completed", "pending"]
    needs_initial_sync: bool = Field(
        default=False,
        description=(
            "True if page has never been synced (status='idle' and no last_sync_at). "
            "False if page has been synced at least once (even if total_synced_posts=0 for empty pages)."
        ),
    )
