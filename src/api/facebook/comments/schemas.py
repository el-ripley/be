from __future__ import annotations

from typing import Dict, Optional, Any, List
from pydantic import BaseModel, Field


class CommentInteractionRequest(BaseModel):
    """Request schema for comment interactions (reply, hide, unhide, delete)."""

    comment_id: str = Field(..., description="Facebook comment ID")
    action: str = Field(
        ..., description="Action to perform: reply, hide, unhide, delete"
    )
    message: Optional[str] = Field(
        None, description="Message for reply action (required for reply)"
    )
    attachment_url: Optional[str] = Field(
        None, description="Optional attachment URL for reply action"
    )


class CommentInteractionResponse(BaseModel):
    """Response schema for comment interactions."""

    success: bool = Field(..., description="Whether the operation was successful")
    comment_id: str = Field(..., description="Facebook comment ID")
    action: str = Field(..., description="Action that was performed")
    message: str = Field(..., description="Operation result message")
    api_response: Optional[Dict[str, Any]] = Field(
        None, description="Facebook API response data"
    )
    new_comment_id: Optional[str] = Field(
        None, description="New comment ID from Graph API (for reply action, for FE optimistic display)"
    )


class Comment(BaseModel):
    """Schema for a Facebook comment."""

    id: str = Field(..., description="Facebook comment ID")
    post_id: str = Field(..., description="Facebook post ID")
    fan_page_id: str = Field(..., description="Facebook page ID")
    parent_comment_id: Optional[str] = Field(
        None, description="Parent comment ID if reply"
    )
    root_comment_id: Optional[str] = Field(None, description="Root comment ID")
    is_from_page: bool = Field(..., description="Whether comment is from page")
    facebook_page_scope_user_id: Optional[str] = Field(
        None, description="Page scope user ID"
    )
    message: Optional[str] = Field(None, description="Comment message")
    photo_url: Optional[str] = Field(None, description="Photo URL if attachment")
    video_url: Optional[str] = Field(None, description="Video URL if attachment")
    facebook_created_time: Optional[int] = Field(
        None, description="Facebook creation timestamp"
    )
    like_count: int = Field(
        default=0, description="Number of likes/reactions on this comment"
    )
    reply_count: int = Field(default=0, description="Number of replies to this comment")
    reactions_fetched_at: Optional[int] = Field(
        None, description="When reactions were last fetched for this comment"
    )
    is_hidden: bool = Field(..., description="Whether comment is hidden")
    page_seen_at: Optional[int] = Field(
        None, description="When page/admin viewed this comment"
    )
    deleted_at: Optional[int] = Field(None, description="Soft delete timestamp")
    created_at: int = Field(..., description="Database creation timestamp")
    updated_at: int = Field(..., description="Database update timestamp")
    author_kind: str = Field(..., description="Author kind: 'page' or 'user'")
    fpsu_id: Optional[str] = Field(None, description="Facebook page scope user ID")
    fpsu_name: Optional[str] = Field(None, description="Facebook page scope user name")
    fpsu_profile_pic: Optional[str] = Field(
        None, description="Facebook page scope user profile pic"
    )
    page_name: Optional[str] = Field(None, description="Page name")
    page_avatar: Optional[str] = Field(None, description="Page avatar")
    page_category: Optional[str] = Field(None, description="Page category")
    post_message: Optional[str] = Field(None, description="Post message")
    metadata: Optional[Dict[str, Any]] = Field(
        None, description="E.g. sent_by: ai_agent|admin, history_id for AI"
    )


class Commenter(BaseModel):
    """Schema for a commenter participant (facebook_scope_user)."""

    fpsu_id: Optional[str] = Field(None, description="Facebook page scope user ID")
    fpsu_name: Optional[str] = Field(None, description="Facebook page scope user name")
    fpsu_profile_pic: Optional[str] = Field(
        None, description="Facebook page scope user profile pic"
    )
    author_kind: str = Field(..., description="Author kind: 'page' or 'user'")


class ConversationParticipant(BaseModel):
    """Participant snapshot within a conversation thread."""

    facebook_page_scope_user_id: Optional[str] = Field(
        None, description="Participant PSID (if user)"
    )
    name: Optional[str] = Field(None, description="Display name if available")
    avatar: Optional[str] = Field(None, description="Profile picture URL if available")
    last_comment_id: Optional[str] = Field(
        None, description="Most recent comment ID from this participant"
    )
    last_comment_time: Optional[int] = Field(
        None, description="Timestamp of participant's latest comment"
    )


class CommentThreadSummary(BaseModel):
    """Summary of a conversation thread (root comment + latest reply)."""

    conversation_id: str = Field(..., description="Conversation UUID")
    root_comment_id: str = Field(..., description="Root Facebook comment ID")
    fan_page_id: str = Field(..., description="Fan page ID")
    post_id: str = Field(..., description="Post ID containing the thread")
    total_comments: int = Field(..., description="Total comments in thread")
    unread_count: int = Field(..., description="Unread user comments count")
    mark_as_read: bool = Field(
        default=False, description="User manually marked as read/unread"
    )
    has_page_reply: bool = Field(..., description="Whether page has replied")
    latest_comment_is_from_page: Optional[bool] = Field(
        None, description="Whether latest comment is from page"
    )
    page: "PageInfo" = Field(..., description="Page information")
    post: "PostInfo" = Field(..., description="Post information")
    root_comment: Comment = Field(..., description="Root comment payload")
    latest_comment: Optional[Comment] = Field(
        None, description="Latest reply payload (if any)"
    )
    participants: List[ConversationParticipant] = Field(
        default_factory=list, description="Participants involved in thread"
    )


class CommentThreadListResponse(BaseModel):
    """Cursor-based response for root comment threads."""

    items: List[CommentThreadSummary] = Field(..., description="Thread summaries")
    has_more: bool = Field(..., description="Whether more data is available")
    next_cursor: Optional[str] = Field(
        None, description="Cursor for fetching the next page"
    )


class PageInfo(BaseModel):
    """Page information schema."""

    id: str = Field(..., description="Page ID")
    name: Optional[str] = Field(None, description="Page name")
    avatar: Optional[str] = Field(None, description="Page avatar URL")
    category: Optional[str] = Field(None, description="Page category")
    created_at: int = Field(..., description="Page creation timestamp")
    updated_at: int = Field(..., description="Page last update timestamp")


class PostInfo(BaseModel):
    """Post information schema."""

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


class CommentThreadResponse(BaseModel):
    """Response schema for comment thread by root comment ID endpoint."""

    comments: List[Comment] = Field(..., description="Comments returned for this page")
    page: PageInfo = Field(..., description="Page information")
    post: PostInfo = Field(..., description="Post information")
    total_count: int = Field(..., description="Total number of comments in thread")
    has_more: bool = Field(..., description="Whether more comments can be fetched")
    next_cursor: Optional[str] = Field(
        None, description="Cursor for fetching additional comments"
    )


class UpdateCommentMarkAsReadRequest(BaseModel):
    """Request schema for updating comment mark_as_read status."""

    mark_as_read: bool = Field(
        ..., description="Whether to mark comment as read or unread"
    )


class UpdateCommentMarkAsReadResponse(BaseModel):
    """Response schema for updating comment mark_as_read status."""

    success: bool = Field(..., description="Whether the operation was successful")
    comment_id: str = Field(..., description="Root comment ID")
    conversation_id: Optional[str] = Field(
        None, description="Conversation ID associated with the root comment"
    )
    mark_as_read: bool = Field(..., description="Updated mark_as_read status")
    message: str = Field(..., description="Operation result message")
    updated_count: Optional[int] = Field(
        None, description="Number of comments affected by the update"
    )
    unread_count: Optional[int] = Field(
        None, description="Updated unread count for the conversation"
    )


class SendMessageToCommenterRequest(BaseModel):
    """Request schema for sending message to commenter."""

    comment_id: str = Field(..., description="Facebook comment ID")
    message: str = Field(..., description="Message content to send to commenter")


class SendMessageToCommenterResponse(BaseModel):
    """Response schema for sending message to commenter."""

    success: bool = Field(..., description="Whether the operation was successful")
    comment_id: str = Field(..., description="Facebook comment ID")
    commenter_id: Optional[str] = Field(
        None, description="Facebook page scope user ID of commenter"
    )
    message: str = Field(..., description="Operation result message")
    api_response: Optional[Dict[str, Any]] = Field(
        None, description="Facebook API response data"
    )


class CommentSocketEventData(BaseModel):
    """Socket event data for comment webhook events (add, edited, remove, hide, unhide)."""

    page_id: str = Field(..., description="Facebook page ID")
    conversation: CommentThreadSummary = Field(
        ..., description="Updated conversation summary with nested page/post/comments"
    )
    mutated_comment: Comment = Field(
        ..., description="The comment that was added/edited/removed/hidden"
    )
    action: str = Field(
        ..., description="Action type: add, edited, remove, hide, unhide"
    )
    timestamp: int = Field(..., description="Unix timestamp of the event")


CommentThreadSummary.model_rebuild()
CommentSocketEventData.model_rebuild()
