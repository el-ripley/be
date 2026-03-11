"""Pydantic schemas for Suggest Response structured output.

These schemas represent the LLM's raw output format (agent input).
The agent uses media_ids to reference images; the runner resolves
these to actual URLs before downstream delivery (FE, GraphAPI).
"""

from typing import List, Optional, Union

from pydantic import BaseModel, Field


class MessageSuggestion(BaseModel):
    """Suggestion schema for messages conversation (agent input format).

    Agent provides media_ids (UUIDs from context) instead of raw URLs.
    The runner resolves media_ids → s3_urls for downstream delivery as image_urls.
    Agent provides reply_to_ref (#N) instead of raw Facebook message IDs.
    The runner resolves reply_to_ref → reply_to_message_id via the message_ref_map.
    """

    message: str = Field(..., description="Text message to send")
    media_ids: Optional[List[str]] = Field(
        None,
        description="List of media asset UUIDs to attach as images (from page_memory/user_memory/conversation context)",
    )
    video_url: Optional[str] = Field(
        None, description="Video URL to send (must be publicly accessible, max 25MB)"
    )
    reply_to_ref: Optional[str] = Field(
        None, description="Short message reference (e.g. '#5') to reply to"
    )


class CommentSuggestion(BaseModel):
    """Suggestion schema for comments conversation (agent input format).

    Agent provides attachment_media_id (UUID from context) instead of raw URL.
    The runner resolves attachment_media_id → s3_url for downstream delivery as attachment_url.
    """

    message: str = Field(..., description="Text message for comment reply")
    attachment_media_id: Optional[str] = Field(
        None,
        description="Media asset UUID for image attachment (from page_memory/conversation context)",
    )


class SuggestResponseOutput(BaseModel):
    """Structured output schema for suggest response agent."""

    suggestions: List[Union[MessageSuggestion, CommentSuggestion]] = Field(
        ..., description="List of response suggestions"
    )
