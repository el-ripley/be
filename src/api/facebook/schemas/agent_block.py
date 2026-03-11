"""Schemas for conversation agent block API."""

from typing import Optional

from pydantic import BaseModel


class AgentBlockResponse(BaseModel):
    """Response for get/upsert agent block."""

    id: Optional[str] = None
    is_blocked: bool
    blocked_by: Optional[
        str
    ] = None  # 'suggest_response_agent' | 'general_agent' | 'user'
    reason: Optional[str] = None
    created_at: Optional[int] = None


class AgentBlockUpsertRequest(BaseModel):
    """Request body for upserting agent block."""

    is_active: bool  # True = block, False = unblock
    reason: Optional[str] = None
