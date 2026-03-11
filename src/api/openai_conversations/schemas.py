"""
OpenAI Conversations API schemas.

Request and response models for OpenAI conversation management endpoints.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.common.types import PaginatedResponse

# ================================================================
# CONVERSATION SCHEMAS
# ================================================================


class CreateConversationRequest(BaseModel):
    """Request schema for creating a new conversation."""

    title: Optional[str] = Field(
        default=None, description="Optional conversation title", max_length=500
    )


class ConversationSettingsSchema(BaseModel):
    """Schema for conversation model settings."""

    model: str = Field(
        ..., description="Model name: gpt-5-mini, gpt-5-nano, gpt-5, or gpt-5.2"
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Reasoning effort: low, medium, high, or none (none only for gpt-5.2)",
    )
    verbosity: Optional[str] = Field(
        default=None, description="Verbosity level: low, medium, or high"
    )
    web_search_enabled: Optional[bool] = Field(
        default=True, description="Enable web search tool (default: True)"
    )


class UpdateConversationSettingsRequest(BaseModel):
    """Request schema for updating conversation settings.

    Accepts either:
    - A string format: "gpt-5-mini reasoning: high, verbosity: high"
    - A structured object with model, reasoning, verbosity fields
    """

    settings: Optional[str] = Field(
        default=None,
        description="Settings string format: 'gpt-5-mini reasoning: high, verbosity: high'",
    )
    model: Optional[str] = Field(
        default=None,
        description="Model name: gpt-5-mini, gpt-5-nano, gpt-5, or gpt-5.2",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Reasoning effort: low, medium, high, or none (none only for gpt-5.2)",
    )
    verbosity: Optional[str] = Field(
        default=None, description="Verbosity level: low, medium, or high"
    )
    web_search_enabled: Optional[bool] = Field(
        default=None, description="Enable web search tool"
    )


class ConversationResponse(BaseModel):
    """Response schema for conversation data."""

    id: str = Field(..., description="Conversation UUID")
    user_id: str = Field(..., description="User UUID")
    title: Optional[str] = Field(default=None, description="Conversation title")
    current_branch_id: Optional[str] = Field(
        default=None, description="Current active branch ID"
    )
    settings: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Conversation model settings (model, reasoning, verbosity)",
    )
    created_at: int = Field(..., description="Creation timestamp (milliseconds)")
    updated_at: int = Field(..., description="Last update timestamp (milliseconds)")

    # Facebook linking data
    linked_fb_conversations: List[str] = Field(
        default_factory=list, description="List of linked Facebook conversation IDs"
    )
    linked_fb_comments: List[str] = Field(
        default_factory=list, description="List of linked Facebook comment IDs"
    )

    class Config:
        from_attributes = True


class BranchResponse(BaseModel):
    """Response schema for conversation branch data."""

    id: str = Field(..., description="Branch UUID")
    conversation_id: str = Field(..., description="Conversation UUID")
    created_from_message_id: Optional[str] = Field(
        default=None, description="Message ID that created this branch"
    )
    created_from_branch_id: Optional[str] = Field(
        default=None, description="Parent branch ID"
    )
    message_ids: List[str] = Field(
        default_factory=list, description="Array of message IDs in this branch"
    )
    branch_name: Optional[str] = Field(default=None, description="Optional branch name")
    is_active: bool = Field(
        default=False, description="Whether this is the active branch"
    )
    created_at: int = Field(..., description="Creation timestamp (milliseconds)")
    updated_at: int = Field(..., description="Last update timestamp (milliseconds)")

    class Config:
        from_attributes = True


class ConversationDetailResponse(ConversationResponse):
    """Detailed response schema for a single conversation."""

    message_sequence_counter: int = Field(
        ..., description="Counter for message sequence numbers"
    )
    oldest_message_id: Optional[str] = Field(
        default=None, description="UUID of the oldest message in conversation"
    )
    branches: List[BranchResponse] = Field(
        default_factory=list,
        description="List of branches associated with this conversation",
    )


class UpdateBranchNameRequest(BaseModel):
    """Request schema for updating a conversation branch name."""

    branch_name: Optional[str] = Field(
        default=None, description="New branch name", max_length=255
    )


class UpdateConversationRequest(BaseModel):
    """Request schema for updating conversation settings."""

    branch_id: Optional[str] = Field(default=None, description="Branch ID to switch to")
    title: Optional[str] = Field(
        default=None, description="Update conversation title", max_length=500
    )


# ================================================================
# MESSAGE SCHEMAS
# ================================================================


class MessageResponse(BaseModel):
    """Response schema for message data."""

    id: str = Field(..., description="Message UUID")
    conversation_id: str = Field(..., description="Conversation UUID")
    sequence_number: int = Field(
        ..., description="Global sequence number within conversation"
    )
    role: str = Field(..., description="Message role")
    type: str = Field(..., description="Message type")
    content: Optional[Any] = Field(default=None, description="Message content payload")
    reasoning_summary: Optional[Any] = Field(
        default=None, description="Reasoning summary payload"
    )
    encrypted_content: Optional[str] = Field(
        default=None, description="Encrypted reasoning payload"
    )
    call_id: Optional[str] = Field(
        default=None,
        description="Call ID linking function_call and function_call_output messages",
    )
    function_name: Optional[str] = Field(
        default=None, description="Function name for function_call messages"
    )
    function_arguments: Optional[Any] = Field(
        default=None, description="Function arguments for function_call messages"
    )
    function_output: Optional[Any] = Field(
        default=None, description="Function output for function_call_output messages"
    )

    # Web search fields (for type='web_search_call')
    web_search_action: Optional[Dict[str, Any]] = Field(
        default=None, description="Web search action details (type, query, sources)"
    )

    status: Optional[str] = Field(default=None, description="Message status")
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata for message classification (can include MessageMetadata fields plus custom fields)",
    )
    created_at: int = Field(..., description="Creation timestamp (milliseconds)")
    updated_at: int = Field(..., description="Update timestamp (milliseconds)")

    # Branch-specific fields
    is_modified: bool = Field(
        default=False, description="Whether message is modified in current branch"
    )
    modified_content: Optional[Any] = Field(
        default=None, description="Modified content for current branch"
    )
    modified_reasoning_summary: Optional[Any] = Field(
        default=None, description="Modified reasoning summary for current branch"
    )
    modified_function_arguments: Optional[Any] = Field(
        default=None, description="Modified function arguments for current branch"
    )
    modified_function_output: Optional[Any] = Field(
        default=None, description="Modified function output for current branch"
    )
    is_hidden: bool = Field(
        default=False, description="Whether message is hidden in current branch"
    )

    class Config:
        from_attributes = True


# ================================================================
# PAGINATION SCHEMAS
# ================================================================


class ConversationsPaginatedResponse(PaginatedResponse[ConversationResponse]):
    """Paginated response for conversations."""

    pass


class ConversationsCursorResponse(BaseModel):
    """Cursor-based response for conversations."""

    items: List[ConversationResponse] = Field(..., description="List of conversations")
    has_more: bool = Field(
        ..., description="Whether there are more conversations to fetch"
    )
    next_cursor: Optional[str] = Field(
        default=None, description="Cursor for next page (conversation ID)"
    )


class MessagesCursorResponse(BaseModel):
    """Cursor-based response for messages."""

    items: List[MessageResponse] = Field(..., description="List of messages")
    has_more: bool = Field(..., description="Whether there are more messages to fetch")
    next_cursor: Optional[int] = Field(
        default=None, description="Cursor for next page (ordinal position in branch)"
    )


# ================================================================
# FACEBOOK LINKING SCHEMAS (removed)
# ================================================================


class FacebookLinkingResponse(BaseModel):
    """Deprecated placeholder; Facebook linking has been removed."""

    success: bool = Field(
        default=False, description="Deprecated flag; linking is no longer supported"
    )
    message: str = Field(
        default="Facebook linking is no longer supported",
        description="Deprecated message field",
    )
    linked_conversations: List[str] = Field(
        default_factory=list, description="Deprecated; always empty"
    )
    linked_comments: List[str] = Field(
        default_factory=list, description="Deprecated; always empty"
    )
