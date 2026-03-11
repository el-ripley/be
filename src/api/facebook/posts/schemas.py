"""Pydantic schemas for Facebook posts API."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# ============================================================================
# POSTS LISTING
# ============================================================================


class PostListItem(BaseModel):
    """Post item in list response."""

    id: str
    fan_page_id: str
    message: Optional[str] = None
    video_link: Optional[str] = None
    photo_link: Optional[str] = None
    facebook_created_time: Optional[int] = None
    full_picture: Optional[str] = None
    permalink_url: Optional[str] = None
    status_type: Optional[str] = None
    is_published: Optional[bool] = True
    reaction_total_count: int = 0
    share_count: int = 0
    comment_count: int = 0
    created_at: int
    updated_at: int
    # Comment sync status
    comment_sync_status: Optional[
        Literal["idle", "in_progress", "completed", "error"]
    ] = None
    total_synced_root_comments: Optional[int] = None
    total_synced_comments: Optional[int] = None
    comment_last_sync_at: Optional[int] = None


class PostsListResponse(BaseModel):
    """Response for listing posts."""

    posts: List[PostListItem]
    has_more: bool
    cursor: Optional[str] = None  # JSON-encoded tuple (time, post_id)


# ============================================================================
# POST DETAIL
# ============================================================================


class PostDetailResponse(BaseModel):
    """Response for getting post detail."""

    id: str = Field(..., description="Post ID")
    fan_page_id: str = Field(..., description="Fan page ID")
    message: Optional[str] = Field(None, description="Post message content")
    video_link: Optional[str] = Field(None, description="Post video URL")
    photo_link: Optional[str] = Field(None, description="Post photo URL")
    facebook_created_time: Optional[int] = Field(
        None, description="Facebook post creation time"
    )
    # Engagement aggregate counts
    reaction_total_count: int = Field(
        default=0, description="Total number of reactions on this post"
    )
    reaction_like_count: int = Field(default=0, description="Number of LIKE reactions")
    reaction_love_count: int = Field(default=0, description="Number of LOVE reactions")
    reaction_haha_count: int = Field(default=0, description="Number of HAHA reactions")
    reaction_wow_count: int = Field(default=0, description="Number of WOW reactions")
    reaction_sad_count: int = Field(default=0, description="Number of SAD reactions")
    reaction_angry_count: int = Field(
        default=0, description="Number of ANGRY reactions"
    )
    reaction_care_count: int = Field(default=0, description="Number of CARE reactions")
    share_count: int = Field(default=0, description="Number of shares")
    comment_count: int = Field(default=0, description="Number of comments")
    # Additional metadata
    full_picture: Optional[str] = Field(None, description="High-resolution image URL")
    permalink_url: Optional[str] = Field(
        None, description="Direct link to post on Facebook"
    )
    status_type: Optional[str] = Field(
        None, description="Post status type (mobile_status_update, added_photos, etc.)"
    )
    is_published: bool = Field(default=True, description="Whether post is published")
    # Tracking timestamps
    reactions_fetched_at: Optional[int] = Field(
        None, description="When reactions were last fetched"
    )
    engagement_fetched_at: Optional[int] = Field(
        None, description="When full engagement data was last fetched"
    )
    created_at: int = Field(..., description="Post creation timestamp")
    updated_at: int = Field(..., description="Post last update timestamp")
