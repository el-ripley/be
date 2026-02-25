from typing import Any, List, Optional

from pydantic import BaseModel, Field


class PageItem(BaseModel):
    page_id: str = Field(..., description="Facebook Page ID")
    name: Optional[str] = Field(None, description="Page name")
    avatar: Optional[str] = Field(None, description="Page avatar URL")
    category: Optional[str] = Field(None, description="Page category")
    tasks: Optional[Any] = Field(None, description="Raw tasks/permissions payload")


class PagesListResponse(BaseModel):
    pages: List[PageItem]


class PageScopeUserItem(BaseModel):
    id: str = Field(..., description="Page-scoped user ID (PSID)")
    fan_page_id: str = Field(..., description="Facebook Page ID")
    user_info: Optional[Any] = Field(None, description="User information JSON")
    created_at: int = Field(..., description="Creation timestamp")
    updated_at: int = Field(..., description="Last update timestamp")


class PageScopeUsersResponse(BaseModel):
    users: List[PageScopeUserItem] = Field(..., description="List of page scope users")
    total: int = Field(..., description="Total number of page scope users")
    page: int = Field(..., description="Current page number")
    limit: int = Field(..., description="Number of items per page")
    has_more: bool = Field(..., description="Whether there are more pages available")
