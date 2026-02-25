"""Handler for escalations API."""

from typing import Any, Dict, Optional

from src.services.agent_comm import EscalationService
from src.utils.logger import get_logger

logger = get_logger()


class EscalationHandler:
    """Handler for escalation list, detail, update, and message endpoints."""

    def __init__(self, escalation_service: EscalationService):
        self.escalation_service = escalation_service

    async def get_escalations(
        self,
        user_id: str,
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
    ) -> Dict[str, Any]:
        """List escalations with filters."""
        return await self.escalation_service.get_escalations(
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

    async def get_escalation_detail(
        self, user_id: str, escalation_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get single escalation with messages."""
        return await self.escalation_service.get_escalation_detail(
            user_id=user_id,
            escalation_id=escalation_id,
        )

    async def update_escalation(
        self,
        user_id: str,
        escalation_id: str,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update escalation status (open/closed)."""
        return await self.escalation_service.update_escalation(
            user_id=user_id,
            escalation_id=escalation_id,
            status=status,
        )

    async def add_escalation_message(
        self,
        user_id: str,
        escalation_id: str,
        content: str,
    ) -> Optional[Dict[str, Any]]:
        """Add a message to an escalation (sender_type = 'user')."""
        return await self.escalation_service.add_message(
            user_id=user_id,
            escalation_id=escalation_id,
            content=content,
        )
