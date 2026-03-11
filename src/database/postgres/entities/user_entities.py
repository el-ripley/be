"""
User domain entity models.

Pure database entity representations for users and roles.
These models represent the complete structure of database records.
"""

from typing import List, Optional

from pydantic import BaseModel, Field

# ================================================================
# ROLE ENTITY
# ================================================================


class Role(BaseModel):
    """Role entity representing user permissions in the database."""

    id: str = Field(..., description="Role UUID")
    name: str = Field(..., description="Role name")

    class Config:
        from_attributes = True


# ================================================================
# USER ENTITY
# ================================================================


class User(BaseModel):
    """User entity representing complete user records in the database."""

    id: str = Field(..., description="User UUID")
    created_at: int = Field(..., description="Creation timestamp")
    updated_at: int = Field(..., description="Update timestamp")
    roles: Optional[List[Role]] = Field(
        default=None, description="User roles (populated when needed)"
    )

    class Config:
        from_attributes = True


# ================================================================
# REFRESH TOKEN ENTITY
# ================================================================


class RefreshToken(BaseModel):
    """Refresh token entity representing long-lived authentication tokens in the database."""

    id: str = Field(..., description="Refresh token UUID")
    user_id: str = Field(..., description="User UUID")
    token: str = Field(..., description="JWT refresh token string")
    expires_at: int = Field(..., description="Token expiration timestamp")
    is_revoked: bool = Field(default=False, description="Whether token is revoked")
    created_at: int = Field(..., description="Creation timestamp")
    updated_at: int = Field(..., description="Update timestamp")

    class Config:
        from_attributes = True
