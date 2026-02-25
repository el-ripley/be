"""Pydantic schemas for socket request validation."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class CustomerTabData(BaseModel):
    """Customer tab data structure (used in agent_trigger payload)."""

    type: Literal["conv_comments", "conv_messages"] = Field(
        ..., description="Type of customer tab"
    )
    id: str = Field(..., description="Conversation ID or comment ID")

    @field_validator("id")
    def _ensure_non_empty_id(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("id must be a string")
        stripped = value.strip()
        if not stripped:
            raise ValueError("id cannot be empty")
        return stripped


class AgentTriggerRequest(BaseModel):
    """Request payload for agent_trigger event."""

    conversation_id: str = Field(..., description="Conversation ID")
    new_human_mes: str = Field(..., description="New human message")
    image_urls: Optional[List[str]] = Field(None, description="Optional image URLs")
    active_tab: Optional[Dict[str, str]] = Field(
        None, description="Active tab type and id"
    )


class AgentQuestionAnswerRequest(BaseModel):
    """Request payload for agent_question_answer event."""

    conversation_id: str = Field(..., description="Conversation ID")
    message_id: str = Field(..., description="ID of the function_call message")
    call_id: str = Field(..., description="call_id of the function_call")
    answers: Optional[Dict[str, str]] = Field(default_factory=dict)
    text: Optional[str] = Field(default="", description="Optional user free text")


class EditHumesRegenerateRequest(BaseModel):
    """Request payload for edit_humes_regenerate event."""

    conversation_id: str = Field(..., description="Conversation ID")
    branch_id: str = Field(..., description="Current branch ID")
    message_id: str = Field(..., description="HuMes message ID to edit")
    edited_content: str = Field(..., description="New text content")
    active_tab: Optional[Dict[str, str]] = Field(None, description="Active tab")


class AgentStopRequest(BaseModel):
    """Request payload for agent_stop event."""

    conversation_id: str = Field(..., description="Conversation ID")
    agent_response_id: str = Field(..., description="Agent response ID to stop")


class GetContextRequest(BaseModel):
    """Request payload for get_context event."""

    conversation_id: str = Field(..., description="Conversation ID")
    include_context: bool = Field(True, description="Include full context in response")
    include_tokens: bool = Field(True, description="Include token count in response")
