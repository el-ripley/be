from typing import Generic, List, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response model"""

    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int

    class Config:
        from_attributes = True
