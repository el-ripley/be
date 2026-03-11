"""Escalations API router."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from src.middleware.auth_middleware import get_current_user_id

from .handler import EscalationHandler
from .schemas import (
    EscalationDetailResponse,
    EscalationItem,
    EscalationListResponse,
    EscalationMessageCreateRequest,
    EscalationMessageItem,
    EscalationUpdateRequest,
)

router = APIRouter(prefix="/escalations", tags=["Escalations"])


def get_escalation_handler(request: Request) -> EscalationHandler:
    """Get EscalationHandler from app state."""
    return EscalationHandler(
        escalation_service=request.app.state.escalation_service,
    )


@router.get("", response_model=EscalationListResponse)
async def list_escalations(
    conversation_type: Optional[str] = None,
    fan_page_id: Optional[str] = None,
    facebook_conversation_messages_id: Optional[str] = None,
    facebook_conversation_comments_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    created_at_from: Optional[int] = None,
    created_at_to: Optional[int] = None,
    limit: int = 20,
    offset: int = 0,
    user_id: str = Depends(get_current_user_id),
    handler: EscalationHandler = Depends(get_escalation_handler),
):
    """List escalations for the current user with optional filters.
    created_at_from/created_at_to: optional Unix timestamp in milliseconds (inclusive).
    """
    try:
        result = await handler.get_escalations(
            user_id=user_id,
            conversation_type=conversation_type,
            fan_page_id=fan_page_id,
            facebook_conversation_messages_id=facebook_conversation_messages_id,
            facebook_conversation_comments_id=facebook_conversation_comments_id,
            status=status,
            priority=priority,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            limit=limit,
            offset=offset,
        )
        items = [EscalationItem(**item) for item in result["items"]]
        return EscalationListResponse(items=items, total=result["total"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{escalation_id}", response_model=EscalationDetailResponse)
async def get_escalation(
    escalation_id: str,
    user_id: str = Depends(get_current_user_id),
    handler: EscalationHandler = Depends(get_escalation_handler),
):
    """Get single escalation with its messages."""
    try:
        detail = await handler.get_escalation_detail(
            user_id=user_id,
            escalation_id=escalation_id,
        )
        if not detail:
            raise HTTPException(status_code=404, detail="Escalation not found")
        messages = [EscalationMessageItem(**m) for m in detail.pop("messages", [])]
        return EscalationDetailResponse(messages=messages, **detail)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{escalation_id}", response_model=EscalationItem)
async def update_escalation(
    escalation_id: str,
    request: EscalationUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    handler: EscalationHandler = Depends(get_escalation_handler),
):
    """Update escalation status (open/closed)."""
    try:
        updated = await handler.update_escalation(
            user_id=user_id,
            escalation_id=escalation_id,
            status=request.status,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Escalation not found")
        return EscalationItem(**updated)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{escalation_id}/messages", response_model=EscalationMessageItem)
async def add_escalation_message(
    escalation_id: str,
    request: EscalationMessageCreateRequest,
    user_id: str = Depends(get_current_user_id),
    handler: EscalationHandler = Depends(get_escalation_handler),
):
    """Add a message to an escalation thread (user responding)."""
    try:
        message = await handler.add_escalation_message(
            user_id=user_id,
            escalation_id=escalation_id,
            content=request.content,
        )
        if not message:
            raise HTTPException(status_code=404, detail="Escalation not found")
        return EscalationMessageItem(**message)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
