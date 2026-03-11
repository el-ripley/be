from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SocketConversationPayload(BaseModel):
    """Schema representing conversation data sent via sockets."""

    conversation_id: str = Field(..., description="Conversation ID (Graph t_*)")
    fan_page_id: str = Field(..., description="Fan page ID")
    facebook_page_scope_user_id: str = Field(..., description="Participant PSID")
    mark_as_read: Optional[bool] = Field(
        None, description="User manually toggled read/unread state (UX feature)"
    )
    conversation_created_at: Optional[int] = Field(
        None, description="Unix timestamp conversation was created"
    )
    conversation_updated_at: Optional[int] = Field(
        None, description="Unix timestamp conversation was last updated"
    )
    total_messages: Optional[int] = Field(
        None, description="Total number of messages recorded for this conversation"
    )
    unread_count: Optional[int] = Field(
        None, description="Number of user messages not yet read by admins"
    )
    latest_message_id: Optional[str] = Field(
        None, description="ID of the latest message in the conversation"
    )
    latest_message_is_from_page: Optional[bool] = Field(
        None, description="Whether the latest message is an echo (from page)"
    )
    latest_message_facebook_time: Optional[int] = Field(
        None, description="Facebook timestamp of latest message"
    )
    page_last_seen_message_id: Optional[str] = Field(
        None, description="Latest user message seen by page"
    )
    page_last_seen_at: Optional[int] = Field(
        None, description="Timestamp when page last viewed the conversation"
    )
    user_seen_at: Optional[int] = Field(
        None, description="Timestamp when user saw the conversation (from FB webhook)"
    )
    participants: Optional[List[Dict[str, Any]]] = Field(
        None, description="Participant snapshot array"
    )
    page_name: Optional[str] = Field(None, description="Fan page name")
    page_avatar: Optional[str] = Field(None, description="Fan page avatar URL")
    page_category: Optional[str] = Field(None, description="Fan page category")
    user_info: Optional[Dict[str, Any]] = Field(
        None, description="facebook_page_scope_user.user_info payload"
    )
    ad_context: Optional[Dict[str, Any]] = Field(
        None,
        description="Ad context from webhook referral: {ad_id, source, type, ad_title, photo_url, video_url, post_id, product_id}. Available when user replies to Facebook ads.",
    )


class SocketMessagePayload(BaseModel):
    """Schema representing message data sent via sockets."""

    id: str = Field(..., description="Message ID (mid)")
    conversation_id: str = Field(
        ..., description="Conversation ID this message belongs to"
    )
    is_echo: bool = Field(
        ..., description="Whether the message originates from the page"
    )
    text: Optional[str] = Field(None, description="Text content")
    photo_url: Optional[str] = Field(None, description="Photo URL if present")
    video_url: Optional[str] = Field(None, description="Video URL if present")
    audio_url: Optional[str] = Field(None, description="Audio URL if present")
    template_data: Optional[Dict[str, Any]] = Field(
        None, description="Template data (postback, entry_point, attachments, etc)"
    )
    photo_media: Optional[Dict[str, Any]] = Field(
        None, description="Resolved photo media metadata used by API"
    )
    facebook_timestamp: Optional[int] = Field(
        None, description="Facebook timestamp for the message"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Message metadata e.g. {sent_by: ai_agent, history_id: ...} for AI-sent messages",
    )
    reply_to_message_id: Optional[str] = Field(
        None,
        description="Facebook message id (mid) this message replies to; for reply-thread UI",
    )
    created_at: int = Field(..., description="Database creation timestamp")
    updated_at: int = Field(..., description="Database update timestamp")
