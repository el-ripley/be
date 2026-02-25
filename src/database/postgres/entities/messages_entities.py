"""
Messages domain entity models.

Pure database entity representations for Facebook Messenger conversations.
These models represent the complete structure of database records.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


# ================================================================
# MESSAGE ENTITY
# ================================================================


class Message(BaseModel):
    """Message entity representing complete Facebook Messenger message records in the database."""

    id: str = Field(..., description="Facebook message ID (mid)")
    fan_page_id: str = Field(..., description="Recipient page ID")
    facebook_page_scope_user_id: Optional[str] = Field(
        None, description="Sender PSID if from user"
    )
    is_echo: bool = Field(..., description="True if message from page")
    text: Optional[str] = Field(None, description="Text content")
    photo_url: Optional[str] = Field(None, description="Photo URL")
    video_url: Optional[str] = Field(None, description="Video URL")
    audio_url: Optional[str] = Field(None, description="Audio URL")
    template_data: Optional[Dict[str, Any]] = Field(
        None, description="Templates and interactive elements"
    )
    facebook_timestamp: Optional[int] = Field(
        None, description="Timestamp from webhook"
    )
    deleted_at: Optional[int] = Field(None, description="Soft delete timestamp")
    created_at: int = Field(..., description="Creation timestamp")
    updated_at: int = Field(..., description="Update timestamp")

    class Config:
        from_attributes = True
