"""
Comments domain entity models.

Pure database entity representations for Facebook posts and comments.
These models represent the complete structure of database records.
Posts exist primarily to provide context for comments.
"""

from typing import Optional
from pydantic import BaseModel, Field


# ================================================================
# POST ENTITY
# ================================================================


class Post(BaseModel):
    """Post entity representing complete Facebook post records in the database."""

    id: str = Field(..., description="Facebook post ID")
    fan_page_id: str = Field(..., description="Fan page ID")
    message: Optional[str] = Field(None, description="Post text content")
    video_link: Optional[str] = Field(None, description="Video URL")
    photo_link: Optional[str] = Field(None, description="Photo URL")
    facebook_created_time: Optional[int] = Field(
        None, description="Facebook created_time"
    )
    created_at: int = Field(..., description="Creation timestamp")
    updated_at: int = Field(..., description="Update timestamp")

    class Config:
        from_attributes = True


# ================================================================
# COMMENT ENTITY
# ================================================================


class Comment(BaseModel):
    """Comment entity representing complete Facebook comment records in the database."""

    id: str = Field(..., description="Facebook comment ID")
    post_id: str = Field(..., description="Facebook post ID")
    fan_page_id: str = Field(..., description="Fan page ID")
    parent_comment_id: Optional[str] = Field(
        None, description="Parent comment ID for replies"
    )
    is_from_page: bool = Field(..., description="True if comment is from page itself")
    facebook_page_scope_user_id: Optional[str] = Field(
        None, description="PSID if from user"
    )
    message: Optional[str] = Field(None, description="Comment text content")
    photo_url: Optional[str] = Field(None, description="Photo URL")
    video_url: Optional[str] = Field(None, description="Video URL")
    facebook_created_time: Optional[int] = Field(
        None, description="Facebook created_time"
    )
    is_hidden: bool = Field(..., description="Whether comment is hidden")
    deleted_at: Optional[int] = Field(None, description="Soft delete timestamp")
    created_at: int = Field(..., description="Creation timestamp")
    updated_at: int = Field(..., description="Update timestamp")

    class Config:
        from_attributes = True
