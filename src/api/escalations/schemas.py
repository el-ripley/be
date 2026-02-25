"""Schemas for escalations API."""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# --- Thread context (for list preview) ---


class PageInfo(BaseModel):
    """Minimal page info for thread preview."""

    id: str
    name: Optional[str] = None
    avatar: Optional[str] = None
    category: Optional[str] = None


class PostInfo(BaseModel):
    """Minimal post info for comments thread preview."""

    id: str
    message: Optional[str] = None
    full_picture: Optional[str] = None
    photo_link: Optional[str] = None


class ConversationParticipant(BaseModel):
    """Participant snapshot for comments thread."""

    facebook_page_scope_user_id: Optional[str] = None
    name: Optional[str] = None
    avatar: Optional[str] = None


class MessagesThreadContext(BaseModel):
    """Thread context for conversation_type=messages."""

    user_info: Optional[Dict[str, Any]] = Field(
        None, description="User info (name, profile_pic, id) of the chat participant"
    )
    page: Optional[PageInfo] = None


class CommentsThreadContext(BaseModel):
    """Thread context for conversation_type=comments."""

    post: Optional[PostInfo] = Field(
        None, description="Post containing the comment thread (avatar/post_image)"
    )
    participants: List[ConversationParticipant] = Field(
        default_factory=list, description="Participants in the thread"
    )
    page: Optional[PageInfo] = None


class EscalationUpdateRequest(BaseModel):
    """Request body for PATCH /escalations/{id}."""

    status: Optional[Literal["open", "closed"]] = None


class EscalationMessageCreateRequest(BaseModel):
    """Request body for POST /escalations/{id}/messages."""

    content: str


class EscalationMessageItem(BaseModel):
    """Single message in an escalation thread."""

    id: str
    escalation_id: str
    sender_type: str
    content: str
    created_at: Optional[int] = None

    class Config:
        from_attributes = True


class EscalationItem(BaseModel):
    """Single escalation in list/detail."""

    id: str
    conversation_type: str
    facebook_conversation_messages_id: Optional[str] = None
    facebook_conversation_comments_id: Optional[str] = None
    fan_page_id: str
    owner_user_id: str
    created_by: str
    subject: str
    priority: str
    status: str
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    suggest_response_history_id: Optional[str] = None
    thread_context: Optional[Union[MessagesThreadContext, CommentsThreadContext]] = (
        Field(
            None,
            description="Thread context for UI preview: user_info+page for messages, post+participants+page for comments",
        )
    )

    class Config:
        from_attributes = True


class EscalationDetailResponse(BaseModel):
    """Response for GET /escalations/{id} - escalation with messages."""

    id: str
    conversation_type: str
    facebook_conversation_messages_id: Optional[str] = None
    facebook_conversation_comments_id: Optional[str] = None
    fan_page_id: str
    owner_user_id: str
    created_by: str
    subject: str
    priority: str
    status: str
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    suggest_response_history_id: Optional[str] = None
    messages: List[EscalationMessageItem] = []


class EscalationListResponse(BaseModel):
    """Response for GET /escalations."""

    items: List[EscalationItem]
    total: int
