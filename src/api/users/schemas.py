"""
Schemas for user operations including user settings and file management.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, validator

from src.agent.common.conversation_settings import SUPPORTED_MODELS


# ================================================================
# USER CONVERSATION SETTINGS SCHEMAS
# ================================================================


class UserConversationSettingsUpdate(BaseModel):
    """Schema for updating user conversation settings."""

    context_token_limit: Optional[int] = Field(
        None,
        gt=0,
        description="Context token limit (must be greater than 0)",
    )
    context_buffer_percent: Optional[int] = Field(
        None,
        ge=0,
        le=100,
        description="Buffer percentage (0-100)",
    )
    summarizer_model: Optional[str] = Field(
        None,
        description=f"Summarizer model name. Supported: {', '.join(SUPPORTED_MODELS)}",
    )
    vision_model: Optional[str] = Field(
        None,
        description=f"Vision model name. Supported: {', '.join(SUPPORTED_MODELS)}",
    )

    @validator("summarizer_model")
    def validate_summarizer_model(cls, v):
        """Validate summarizer model is supported."""
        if v is not None and v not in SUPPORTED_MODELS:
            raise ValueError(
                f"Invalid summarizer_model: {v}. Supported models: {', '.join(SUPPORTED_MODELS)}"
            )
        return v

    @validator("vision_model")
    def validate_vision_model(cls, v):
        """Validate vision model is supported."""
        if v is not None and v not in SUPPORTED_MODELS:
            raise ValueError(
                f"Invalid vision_model: {v}. Supported models: {', '.join(SUPPORTED_MODELS)}"
            )
        return v


class UserConversationSettingsResponse(BaseModel):
    """Schema for user conversation settings response."""

    context_token_limit: int = Field(
        ..., description="Context token limit (defaults to system default if not set)"
    )
    context_buffer_percent: int = Field(
        ...,
        description="Buffer percentage (defaults to system default if not set)",
    )
    summarizer_model: str = Field(
        ...,
        description="Summarizer model (defaults to system default if not set)",
    )
    vision_model: str = Field(
        ...,
        description="Vision model (defaults to system default if not set)",
    )


# ================================================================
# USER FILES SCHEMAS
# ================================================================


class FileUploadResult(BaseModel):
    """Result of a single file upload"""

    success: bool = Field(..., description="Whether the upload was successful")
    filename: str = Field(..., description="Original filename")
    url: Optional[str] = Field(
        None, description="S3 URL of the uploaded file (if successful)"
    )
    file_id: Optional[str] = Field(
        None, description="Media asset ID from database (if successful)"
    )
    file_type: Optional[str] = Field(None, description="File type: 'image' or 'video'")
    file_size: Optional[int] = Field(None, description="File size in bytes")
    description: Optional[str] = Field(None, description="Description for the media")
    error: Optional[str] = Field(None, description="Error message (if unsuccessful)")


class FileUploadResponse(BaseModel):
    """Response schema for uploading files"""

    success: bool = Field(
        ..., description="Whether the overall operation was successful"
    )
    results: List[FileUploadResult] = Field(
        ..., description="Upload results for each file"
    )
    successful_uploads: int = Field(
        ..., description="Number of successfully uploaded files"
    )
    total_files: int = Field(..., description="Total number of files processed")
    message: str = Field(..., description="Summary message")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "results": [
                    {
                        "success": True,
                        "filename": "vacation_photo.jpg",
                        "url": "https://bucket.s3.region.amazonaws.com/ephemeral/one_day/uuid.jpg",
                        "file_id": "123e4567-e89b-12d3-a456-426614174000",
                        "file_type": "image",
                        "file_size": 1024000,
                        "error": None,
                    },
                    {
                        "success": False,
                        "filename": "large_video.mp4",
                        "url": None,
                        "file_type": None,
                        "file_size": None,
                        "error": "File size exceeds maximum limit",
                    },
                ],
                "successful_uploads": 1,
                "total_files": 2,
                "message": "Successfully uploaded 1 out of 2 files",
            }
        }


class PromptReference(BaseModel):
    """Reference to a prompt that uses this media"""

    prompt_type: str = Field(
        ..., description="Prompt type: 'page_prompt' or 'page_scope_user_prompt'"
    )
    prompt_id: str = Field(..., description="Prompt UUID")
    display_order: int = Field(..., description="Display order in the prompt")


class MediaItemResponse(BaseModel):
    """Response schema for a single media item"""

    id: str = Field(..., description="Media asset UUID")
    s3_url: str = Field(..., description="S3 URL of the media")
    description: Optional[str] = Field(None, description="Description for the media")
    media_type: str = Field(..., description="Media type: 'image', 'video', or 'audio'")
    mime_type: Optional[str] = Field(None, description="MIME type")
    file_size_bytes: int = Field(..., description="File size in bytes")
    retention_policy: str = Field(..., description="Retention policy")
    expires_at: Optional[int] = Field(
        None, description="Expiration timestamp (milliseconds)"
    )
    created_at: int = Field(..., description="Creation timestamp (milliseconds)")
    updated_at: int = Field(..., description="Last update timestamp (milliseconds)")
    prompts: List[PromptReference] = Field(
        default=[], description="List of prompts that use this media"
    )


class ListMediaResponse(BaseModel):
    """Response schema for listing media"""

    media: List[MediaItemResponse] = Field(..., description="List of media items")
    total: int = Field(..., description="Total number of media items")
    limit: int = Field(..., description="Limit per page")
    offset: int = Field(..., description="Offset for pagination")


class DeleteMediaResponse(BaseModel):
    """Response schema for deleting media"""

    deleted_count: int = Field(..., description="Number of media items deleted")
    media_ids: List[str] = Field(..., description="List of deleted media IDs")
    quota_decreased_bytes: int = Field(
        ..., description="Quota decreased in bytes (for permanent media)"
    )


# ================================================================
# USER MEMORY SCHEMAS (view and delete only)
# ================================================================


class MemoryBlockItem(BaseModel):
    """Single memory block in user memory."""

    id: str
    block_key: str
    title: str
    content: str
    display_order: int
    created_at: int
    created_by_type: str


class UserMemoryResponse(BaseModel):
    """Response for GET /users/memory (active user memory with blocks)."""

    id: Optional[str] = None
    is_active: bool = True
    created_at: Optional[int] = None
    created_by_type: Optional[str] = None
    blocks: List[MemoryBlockItem] = []
