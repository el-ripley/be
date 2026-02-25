"""
Facebook domain entity models.

Pure database entity representations for Facebook integration tables.
These models represent the complete structure of Facebook-related database records.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


# ================================================================
# FACEBOOK APP SCOPE USER ENTITY (ASID)
# ================================================================


class FacebookAppScopeUser(BaseModel):
    """Facebook app scope user entity representing complete ASID records in the database."""

    id: str = Field(..., description="Facebook ASID")
    user_id: str = Field(..., description="Associated user UUID")
    name: Optional[str] = Field(None, description="Facebook name")
    gender: Optional[str] = Field(None, description="Facebook gender")
    email: Optional[str] = Field(None, description="Facebook email")
    picture: Optional[str] = Field(None, description="Facebook picture URL")
    created_at: int = Field(..., description="Creation timestamp")
    updated_at: int = Field(..., description="Update timestamp")

    class Config:
        from_attributes = True


# ================================================================
# FAN PAGE ENTITY
# ================================================================


class FanPage(BaseModel):
    """Fan page entity representing complete Facebook page records in the database."""

    id: str = Field(..., description="Facebook page ID")
    name: Optional[str] = Field(None, description="Page name")
    avatar: Optional[str] = Field(None, description="Page avatar URL")
    category: Optional[str] = Field(None, description="Page category")
    created_at: int = Field(..., description="Creation timestamp")
    updated_at: int = Field(..., description="Update timestamp")

    class Config:
        from_attributes = True


# ================================================================
# FACEBOOK PAGE ADMIN ENTITY
# ================================================================


class FacebookPageAdmin(BaseModel):
    """Facebook page admin entity representing complete admin relationship records in the database."""

    id: str = Field(..., description="Admin relationship UUID")
    facebook_user_id: str = Field(..., description="Facebook user ASID")
    page_id: str = Field(..., description="Fan page ID")
    access_token: str = Field(..., description="Page access token")
    tasks: Optional[Dict[str, Any]] = Field(None, description="Admin tasks/permissions")
    created_at: int = Field(..., description="Creation timestamp")
    updated_at: int = Field(..., description="Update timestamp")

    class Config:
        from_attributes = True


# ================================================================
# FACEBOOK PAGE SCOPE USER ENTITY (PSID)
# ================================================================


class FacebookPageScopeUser(BaseModel):
    """Facebook page scope user entity representing complete PSID records in the database."""

    id: str = Field(..., description="Facebook PSID")
    fan_page_id: str = Field(..., description="Associated fan page ID")
    user_info: Optional[Dict[str, Any]] = Field(None, description="User information")
    created_at: int = Field(..., description="Creation timestamp")
    updated_at: int = Field(..., description="Update timestamp")

    class Config:
        from_attributes = True
