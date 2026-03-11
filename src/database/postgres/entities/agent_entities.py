"""
LLM domain entity models for agent system.

Pure database entity representations for LLM calls, conversations, and messages.
These models represent the complete structure of database records for OpenAI API calls
and agent conversation management.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.agent.common.metadata_types import MessageMetadata

# ================================================================
# LLM CALL ENTITY
# ================================================================


class OpenAIResponse(BaseModel):
    """LLM call entity representing complete LLM call records in the database."""

    # Primary identification
    id: str = Field(..., description="LLM call UUID")
    response_id: str = Field(..., description="OpenAI response ID")
    previous_response_id: Optional[str] = Field(
        default=None,
        description="Previous response ID for conversation chain (optional - manual state management)",
    )

    # User context
    user_id: str = Field(..., description="User UUID")

    # Model & timing
    model: str = Field(..., description="Model name (e.g., gpt-5-mini, gpt-4o)")
    created_at: int = Field(..., description="OpenAI timestamp (milliseconds)")
    latency_ms: Optional[int] = Field(
        default=None, description="API call latency in milliseconds"
    )

    # Token usage
    input_tokens: int = Field(default=0, description="Number of input tokens")
    output_tokens: int = Field(default=0, description="Number of output tokens")
    total_tokens: int = Field(default=0, description="Total tokens used")
    cached_tokens: int = Field(default=0, description="Number of cached tokens")
    reasoning_tokens: int = Field(
        default=0, description="Number of reasoning tokens (o1 models)"
    )

    # Cost tracking (USD)
    input_cost: Decimal = Field(
        default=Decimal("0"), description="Input token cost in USD"
    )
    output_cost: Decimal = Field(
        default=Decimal("0"), description="Output token cost in USD"
    )
    total_cost: Decimal = Field(default=Decimal("0"), description="Total cost in USD")

    # Request/Response data
    input: Optional[Dict[str, Any]] = Field(
        default=None, description="Full input messages/prompts"
    )
    output: Optional[Dict[str, Any]] = Field(
        default=None, description="Full response output"
    )
    tools: Optional[Dict[str, Any]] = Field(
        default=None, description="Tools used in this call"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional context"
    )

    # Status tracking
    status: str = Field(..., description="Call status (completed, failed, in_progress)")
    error: Optional[Dict[str, Any]] = Field(
        default=None, description="Error details if failed"
    )

    # Timestamps
    logged_at: int = Field(
        ..., description="When this record was logged (milliseconds)"
    )

    class Config:
        from_attributes = True
        json_encoders = {
            Decimal: str  # Convert Decimal to string for JSON serialization
        }


# ================================================================
# CONVERSATION ENTITY
# ================================================================


class OpenAIConversation(BaseModel):
    """OpenAI conversation entity representing conversation threads."""

    # Primary identification
    id: str = Field(..., description="Conversation UUID")
    user_id: str = Field(..., description="User UUID")

    # Conversation metadata
    title: Optional[str] = Field(
        default=None, description="Optional conversation title"
    )
    current_branch_id: Optional[str] = Field(
        default=None, description="Current active branch ID"
    )

    # Message sequencing
    message_sequence_counter: int = Field(
        default=0, description="Counter for message sequence numbers"
    )
    oldest_message_id: Optional[str] = Field(
        default=None, description="UUID of the oldest message in conversation"
    )

    # Model settings
    settings: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Conversation model settings (model, reasoning, verbosity)",
    )

    # Subagent support
    parent_conversation_id: Optional[str] = Field(
        default=None, description="Parent conversation ID (for subagents)"
    )
    parent_agent_response_id: Optional[str] = Field(
        default=None, description="Agent response that spawned this subagent"
    )
    subagent_type: Optional[str] = Field(
        default=None, description="Subagent type (e.g., 'explore')"
    )
    is_subagent: Optional[bool] = Field(
        default=False, description="True if this is a subagent context"
    )
    task_call_id: Optional[str] = Field(
        default=None, description="task call ID that spawned this subagent"
    )

    # Timestamps
    created_at: int = Field(..., description="Creation timestamp (milliseconds)")
    updated_at: int = Field(..., description="Last update timestamp (milliseconds)")

    class Config:
        from_attributes = True


# ================================================================
# MESSAGE ENTITY
# ================================================================


class OpenAIMessage(BaseModel):
    """
    OpenAI message entity supporting all Response API message types.
    Types: message, reasoning, function_call, function_call_output, user_input
    """

    # Primary identification
    id: str = Field(..., description="Message UUID")
    conversation_id: str = Field(..., description="Conversation UUID")

    # Sequencing
    sequence_number: int = Field(
        ..., description="Global sequence number within conversation for ordering"
    )

    # Message type & role
    type: str = Field(..., description="Message type")
    role: str = Field(
        ..., description="Message role (system, developer, user, assistant, tool)"
    )

    # Content fields (for type='message' or 'user_input')
    content: Optional[Any] = Field(
        default=None,
        description="Message content (can be string, dict, or other types)",
    )

    # Reasoning fields (for type='reasoning')
    reasoning_summary: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Summary array for reasoning"
    )
    encrypted_content: Optional[str] = Field(
        default=None, description="Encrypted reasoning content"
    )

    # Function call fields (for type='function_call')
    function_name: Optional[str] = Field(default=None, description="Function name")
    function_arguments: Optional[Dict[str, Any]] = Field(
        default=None, description="Function arguments as JSON"
    )
    call_id: Optional[str] = Field(
        default=None, description="Call ID linking function_call to output"
    )

    # Function output fields (for type='function_call_output')
    function_output: Optional[Dict[str, Any]] = Field(
        default=None, description="Function result as JSON"
    )

    # Web search fields (for type='web_search_call')
    web_search_action: Optional[Dict[str, Any]] = Field(
        default=None, description="Web search action details (type, query, sources)"
    )

    # Metadata
    metadata: Optional[MessageMetadata] = Field(
        default=None, description="Optional metadata for message classification"
    )

    # Status
    status: Optional[str] = Field(default=None, description="Message status")

    # Timestamps
    created_at: int = Field(..., description="Creation timestamp (milliseconds)")
    updated_at: int = Field(..., description="Update timestamp (milliseconds)")

    class Config:
        from_attributes = True
