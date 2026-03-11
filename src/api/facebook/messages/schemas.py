from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class MarkAsReadRequest(BaseModel):
    """Request model for marking conversation as read/unread."""

    mark_as_read: bool = Field(
        default=True, description="True to mark as read, False to mark as unread"
    )


class ConversationResponse(BaseModel):
    """Response model for conversation operations."""

    success: bool
    message: Optional[str] = None
    conversation: Optional[Dict[str, Any]] = None


class ConversationData(BaseModel):
    """Conversation data model."""

    conversation_id: str
    fan_page_id: str
    facebook_page_scope_user_id: str
    mark_as_read: bool
    conversation_created_at: int
    conversation_updated_at: int
    page_name: Optional[str] = None
    page_avatar: Optional[str] = None
    page_category: Optional[str] = None
    user_info: Optional[Dict[str, Any]] = None
    total_messages: Optional[int] = Field(
        None, description="Total number of messages recorded for this conversation"
    )
    unread_count: Optional[int] = Field(
        None, description="Number of user messages not yet read by admins"
    )
    participants: Optional[List[Dict[str, Any]]] = None
    ad_context: Optional[Dict[str, Any]] = Field(
        None,
        description="Ad context from webhook referral: {ad_id, source, type, ad_title, photo_url, video_url, post_id, product_id}. Available when user replies to Facebook ads.",
    )

    class Config:
        from_attributes = True


class LatestMessageData(BaseModel):
    """Latest message data model."""

    id: str
    conversation_id: str
    is_echo: bool
    text: Optional[str] = None
    photo_url: Optional[str] = None
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    template_data: Optional[Dict[str, Any]] = None
    facebook_timestamp: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="e.g. {sent_by: 'ai_agent', history_id: '...'} for AI-sent messages",
    )
    reply_to_message_id: Optional[str] = Field(
        None,
        description="Facebook message id (mid) this message replies to; null when not a reply.",
    )
    created_at: int
    updated_at: int

    class Config:
        from_attributes = True


class ConversationItem(BaseModel):
    """Single conversation item model with latest message."""

    conversation: ConversationData
    latest_message: Optional[LatestMessageData] = None

    class Config:
        from_attributes = True


class ConversationsListResponse(BaseModel):
    """Cursor-based response model for conversations."""

    items: List[ConversationItem]
    has_more: bool
    next_cursor: Optional[str] = None


class SendMessageRequest(BaseModel):
    """Request model for sending a message."""

    conversation_id: str = Field(
        description="ID of the conversation to send message to"
    )

    message: Optional[str] = Field(default=None, description="Text message to send")
    image_urls: Optional[List[str]] = Field(
        default=None,
        description="List of image URLs to send (must be publicly accessible)",
    )
    video_url: Optional[str] = Field(
        default=None,
        description="Video URL to send (must be publicly accessible, max 25MB)",
    )
    metadata: Optional[str] = Field(
        default=None,
        description="Optional metadata to include with the message",
    )
    reply_to_message_id: Optional[str] = Field(
        default=None,
        description="Optional Facebook message id (mid) to reply to; message will appear as reply in Messenger",
    )

    @model_validator(mode="after")
    def validate_required_fields(self):
        """Validate that required fields are provided."""
        if not self.message and not self.image_urls and not self.video_url:
            raise ValueError(
                "Either message, image_urls, or video_url must be provided"
            )

        return self


class SendMessageResponse(BaseModel):
    """Response model for sending a message."""

    success: bool
    message: str
    conversation: Optional[Dict[str, Any]] = None


class MessageItem(BaseModel):
    """Single message item model."""

    id: str
    conversation_id: str
    is_echo: bool
    text: Optional[str] = None
    photo_url: Optional[str] = None
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    template_data: Optional[Dict[str, Any]] = None
    facebook_timestamp: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="e.g. {sent_by: 'ai_agent', history_id: '...'} for AI-sent messages",
    )
    reply_to_message_id: Optional[str] = Field(
        None,
        description="Facebook message id (mid) this message replies to; null when not a reply. Use for reply-thread UI.",
    )
    created_at: int
    updated_at: int
    photo_media: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class MessagesListResponse(BaseModel):
    """Cursor-based response model for messages."""

    items: List[MessageItem]
    has_more: bool
    next_cursor: Optional[str] = None
