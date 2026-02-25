"""
Pydantic schemas for Suggest Response API.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, Literal, List, Union


# ================================================================
# AGENT SETTINGS SCHEMAS
# ================================================================


class AgentSettingsUpdate(BaseModel):
    """Schema for updating suggest response agent settings."""

    settings: Optional[Dict[str, Any]] = Field(
        None,
        description="Agent settings (model, reasoning, verbosity). Same structure as openai_conversation.settings",
    )
    allow_auto_suggest: Optional[bool] = Field(
        None, description="Whether to allow automatic suggest response"
    )
    num_suggest_response: Optional[int] = Field(
        None,
        ge=1,
        le=10,
        description="Number of suggest responses to generate per request (1-10)",
    )


class AgentSettingsResponse(BaseModel):
    """Schema for suggest response agent settings response."""

    id: Optional[str] = Field(
        None, description="Agent settings UUID (null if using defaults)"
    )
    user_id: str = Field(..., description="User ID")
    settings: Dict[str, Any] = Field(
        ..., description="Agent settings (model, reasoning, verbosity)"
    )
    allow_auto_suggest: bool = Field(
        ..., description="Whether automatic suggest response is allowed"
    )
    num_suggest_response: int = Field(
        ..., description="Number of suggest responses per request"
    )
    created_at: Optional[int] = Field(
        None, description="Creation timestamp (milliseconds)"
    )
    updated_at: Optional[int] = Field(
        None, description="Last update timestamp (milliseconds)"
    )

    class Config:
        from_attributes = True


# ================================================================
# PAGE ADMIN SUGGEST CONFIG SCHEMAS
# ================================================================


class PageAdminSuggestConfigUpdate(BaseModel):
    """Schema for updating page admin suggest config (webhook automation)."""

    settings: Optional[Dict[str, Any]] = Field(
        None,
        description="Agent settings for this page (model, reasoning, verbosity)",
    )
    auto_webhook_suggest: Optional[bool] = Field(
        None,
        description="Auto-trigger suggest when webhook arrives (suggest only, requires admin online)",
    )
    auto_webhook_graph_api: Optional[bool] = Field(
        None,
        description="Auto-trigger suggest and send reply via Graph API when webhook arrives",
    )
    webhook_delay_seconds: Optional[int] = Field(
        None,
        ge=0,
        description="Debounce delay in seconds for webhook triggers (0 = immediate)",
    )


class PageAdminSuggestConfigResponse(BaseModel):
    """Schema for page admin suggest config response."""

    id: Optional[str] = Field(None, description="Config UUID (null if using defaults)")
    page_admin_id: str = Field(..., description="Page admin ID")
    settings: Dict[str, Any] = Field(
        ..., description="Agent settings (model, reasoning, verbosity)"
    )
    auto_webhook_suggest: bool = Field(
        ..., description="Auto-trigger suggest when webhook arrives"
    )
    auto_webhook_graph_api: bool = Field(
        ..., description="Auto-trigger and send via Graph API when webhook arrives"
    )
    webhook_delay_seconds: int = Field(
        ..., description="Debounce delay in seconds for webhook triggers (0 = immediate)"
    )
    created_at: Optional[int] = Field(None, description="Creation timestamp")
    updated_at: Optional[int] = Field(None, description="Last update timestamp")

    class Config:
        from_attributes = True


# ================================================================
# ASSIGNED PLAYBOOKS SCHEMAS (READ-ONLY)
# ================================================================


class AssignedPlaybookItem(BaseModel):
    """One playbook assigned to a page for a conversation type."""

    id: str = Field(..., description="Playbook UUID")
    title: str = Field(..., description="Playbook title")
    situation: str = Field(..., description="When this playbook applies (trigger condition)")
    content: str = Field(..., description="Guidance content (how to handle the situation)")


class AssignedPlaybooksResponse(BaseModel):
    """Response for list of playbooks assigned to a page + conversation type."""

    playbooks: List[AssignedPlaybookItem] = Field(
        default_factory=list,
        description="Playbooks assigned to this page for the given conversation_type",
    )


# ================================================================
# PAGE MEMORY SCHEMAS (READ-ONLY, RENDERED FORMAT)
# ================================================================


class PageMemoryResponse(BaseModel):
    """Schema for page memory response (rendered text format, same as agent sees)."""

    prompt_id: str = Field(..., description="Prompt UUID")
    fan_page_id: str = Field(..., description="Facebook page ID")
    prompt_type: Literal["messages", "comments"] = Field(..., description="Prompt type")
    rendered_content: str = Field(
        ...,
        description="Rendered memory content in markdown format (same format as agent sees)",
    )
    block_count: int = Field(..., description="Number of memory blocks in this prompt")
    is_active: bool = Field(..., description="Whether this prompt is active")
    created_at: int = Field(..., description="Creation timestamp (milliseconds)")


# ================================================================
# USER MEMORY SCHEMAS (READ-ONLY, RENDERED FORMAT)
# ================================================================


class UserMemoryResponse(BaseModel):
    """Schema for user memory response (rendered text format, same as agent sees)."""

    prompt_id: str = Field(..., description="Prompt UUID")
    fan_page_id: str = Field(..., description="Facebook page ID")
    psid: str = Field(..., description="Page-scoped user ID (PSID)")
    rendered_content: str = Field(
        ...,
        description="Rendered memory content in markdown format (same format as agent sees)",
    )
    block_count: int = Field(..., description="Number of memory blocks in this prompt")
    is_active: bool = Field(..., description="Whether this prompt is active")
    created_at: int = Field(..., description="Creation timestamp (milliseconds)")


# ================================================================
# GENERATE SUGGESTIONS SCHEMAS
# ================================================================


class GenerateSuggestionsRequest(BaseModel):
    """Request schema for generating response suggestions."""

    conversation_type: Literal["messages", "comments"] = Field(
        ..., description="Type of conversation: 'messages' or 'comments'"
    )
    conversation_id: str = Field(
        ...,
        description=(
            "Conversation ID: "
            "For messages: facebook_conversation_messages.id (UUID as string). "
            "For comments: facebook_conversation_comments.id (UUID) or root_comment_id (Facebook comment ID format like 'post_id_comment_id')"
        ),
    )
    trigger_type: Literal["user", "auto"] = Field(
        default="user",
        description="How this suggestion was triggered: 'user' or 'auto'",
    )
    hint: Optional[str] = Field(
        default=None,
        description="Raw instruction text to inject into suggest_response context as guidance (e.g. for testing before writing playbooks)",
    )

    @field_validator("conversation_type", mode="before")
    @classmethod
    def normalize_conversation_type(cls, v: Any) -> str:
        """
        Normalize conversation_type to handle common typos.
        'comment' -> 'comments' (backward compatibility)
        """
        if isinstance(v, str):
            # Normalize common typos
            if v.lower() == "comment":
                return "comments"
            if v.lower() == "message":
                return "messages"
        return v


class SuggestionItem(BaseModel):
    """Single suggestion item schema."""

    message: str = Field(..., description="Text message")
    image_urls: Optional[List[str]] = Field(
        None, description="List of image URLs (for messages only)"
    )
    video_url: Optional[str] = Field(None, description="Video URL (for messages only)")
    attachment_url: Optional[str] = Field(
        None, description="Attachment URL - image or video (for comments only)"
    )


class GenerateSuggestionsResponse(BaseModel):
    """Response schema for generating response suggestions."""

    history_id: Optional[str] = Field(
        None, description="Suggest response history record UUID (None if skipped)"
    )
    suggestions: List[SuggestionItem] = Field(
        ..., description="List of generated suggestions"
    )
    suggestion_count: int = Field(..., description="Number of suggestions generated")
    skipped: bool = Field(
        default=False,
        description="True if auto-trigger was skipped due to no content changes",
    )
    locked: bool = Field(
        default=False,
        description="True if generation was blocked because another request is in progress",
    )


# ================================================================
# SUGGEST RESPONSE HISTORY SCHEMAS
# ================================================================


class SuggestResponseHistoryItem(BaseModel):
    """Schema for a single suggest response history record."""

    id: str = Field(..., description="History record UUID")
    user_id: str = Field(..., description="User ID who triggered this suggestion")
    fan_page_id: str = Field(
        ..., description="Facebook page ID where suggestion was created"
    )
    conversation_type: Literal["messages", "comments"] = Field(
        ..., description="Type of conversation"
    )
    facebook_conversation_messages_id: Optional[str] = Field(
        None, description="Facebook conversation messages ID (for messages type)"
    )
    facebook_conversation_comments_id: Optional[str] = Field(
        None, description="Facebook conversation comments ID (for comments type)"
    )
    latest_item_id: str = Field(
        ..., description="Latest message/comment ID at time of suggestion"
    )
    latest_item_facebook_time: int = Field(
        ..., description="Facebook timestamp of latest item (milliseconds)"
    )
    page_prompt_id: Optional[str] = Field(
        None, description="Page prompt ID used (if any)"
    )
    page_scope_user_prompt_id: Optional[str] = Field(
        None, description="Page-scope user prompt ID used (if any, only for messages)"
    )
    suggestions: List[SuggestionItem] = Field(
        ..., description="List of generated suggestions"
    )
    suggestion_count: int = Field(..., description="Number of suggestions generated")
    agent_response_id: str = Field(
        ..., description="Agent response ID for technical details"
    )
    trigger_type: Literal[
        "user", "auto", "webhook_suggest", "webhook_auto_reply", "general_agent"
    ] = Field(..., description="How this suggestion was triggered")
    selected_suggestion_index: Optional[int] = Field(
        None, description="Index of selected suggestion (0-based, None if not selected)"
    )
    reaction: Optional[Literal["like", "dislike"]] = Field(
        None, description="User reaction to the suggestions (like/dislike)"
    )
    created_at: int = Field(..., description="Creation timestamp (milliseconds)")
    updated_at: int = Field(..., description="Last update timestamp (milliseconds)")

    class Config:
        from_attributes = True


class SuggestResponseHistoryResponse(BaseModel):
    """Response schema for getting a single suggest response history record."""

    history: SuggestResponseHistoryItem = Field(..., description="History record")


class SuggestResponseHistoryListResponse(BaseModel):
    """Response schema for listing suggest response history records."""

    history: List[SuggestResponseHistoryItem] = Field(
        ..., description="List of history records"
    )
    total: int = Field(..., description="Total number of records (for pagination)")


# ================================================================
# SUGGEST RESPONSE MESSAGE SCHEMAS (AGENT EXECUTION STEPS)
# ================================================================


class SuggestResponseMessageItem(BaseModel):
    """Schema for a single suggest response message (agent execution step)."""

    id: str = Field(..., description="Message record UUID")
    history_id: str = Field(..., description="History record UUID")
    sequence_number: int = Field(..., description="Order within agent run (0-based)")
    role: str = Field(..., description="Message role: 'assistant', 'tool'")
    type: str = Field(
        ...,
        description="Message type: 'reasoning', 'function_call', 'function_call_output', 'text'",
    )
    content: Optional[Dict[str, Any]] = Field(None, description="Content (JSONB)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Optional metadata")
    reasoning_summary: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = Field(
        None,
        description="Reasoning summary (for type='reasoning'); API returns list of {text, type}",
    )
    call_id: Optional[str] = Field(
        None, description="Links function_call to function_call_output"
    )
    function_name: Optional[str] = Field(
        None, description="Function name (for function_call)"
    )
    function_arguments: Optional[Dict[str, Any]] = Field(
        None, description="Function arguments (for function_call)"
    )
    function_output: Optional[Dict[str, Any]] = Field(
        None, description="Function output (for function_call_output)"
    )
    web_search_action: Optional[Dict[str, Any]] = Field(
        None, description="Web search action details"
    )
    status: Optional[str] = Field(
        None, description="Status: 'completed', 'failed', 'in_progress'"
    )
    step: Optional[str] = Field(
        None,
        description="Which step produced this message: 'playbook_retrieval' or 'response_generation'. Default 'response_generation' for older records.",
    )
    created_at: int = Field(..., description="Creation timestamp (milliseconds)")

    class Config:
        from_attributes = True


class SuggestResponseMessageListResponse(BaseModel):
    """Response schema for listing suggest response messages by history_id."""

    messages: List[SuggestResponseMessageItem] = Field(
        ..., description="List of message items (ordered by sequence_number)"
    )


# ================================================================
# UPDATE HISTORY REQUEST
# ================================================================


class UpdateSuggestResponseHistoryRequest(BaseModel):
    """Request schema for updating suggest response history."""

    selected_suggestion_index: Optional[int] = Field(
        None,
        ge=0,
        description="Index of selected suggestion (0-based). Set to null to clear selection.",
    )
    reaction: Optional[Literal["like", "dislike"]] = Field(
        None,
        description="User reaction: 'like' or 'dislike'. Set to null to clear reaction.",
    )
