"""
Socket emission service for Facebook messages.

Handles building payloads and emitting events to page admins via SocketService.
"""

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.utils.logger import get_logger

from .socket_schemas import SocketConversationPayload, SocketMessagePayload

if TYPE_CHECKING:
    from src.socket_service import SocketService

logger = get_logger()


class SocketEmitter:
    """Handles socket event emissions for message-related events."""

    def __init__(self, socket_service: "SocketService"):
        self.socket_service = socket_service

    def build_conversation_payload(
        self, conversation_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build normalized conversation payload for socket emissions.

        Args:
            conversation_data: Raw conversation data from database

        Returns:
            Validated and serialized conversation payload
        """
        participants = conversation_data.get("participants_snapshot") or []
        if isinstance(participants, str):
            try:
                participants = json.loads(participants)
            except json.JSONDecodeError:
                participants = []

        payload = {
            "conversation_id": conversation_data["conversation_id"],
            "fan_page_id": conversation_data["fan_page_id"],
            "facebook_page_scope_user_id": conversation_data[
                "facebook_page_scope_user_id"
            ],
            "mark_as_read": conversation_data.get("mark_as_read"),
            "conversation_created_at": conversation_data.get("conversation_created_at"),
            "conversation_updated_at": conversation_data.get("conversation_updated_at"),
            "total_messages": conversation_data.get("total_messages"),
            "unread_count": conversation_data.get("unread_count"),
            "latest_message_id": conversation_data.get("latest_message_id"),
            "latest_message_is_from_page": conversation_data.get(
                "latest_message_is_from_page"
            ),
            "latest_message_facebook_time": conversation_data.get(
                "latest_message_facebook_time"
            ),
            "page_last_seen_message_id": conversation_data.get(
                "page_last_seen_message_id"
            ),
            "page_last_seen_at": conversation_data.get("page_last_seen_at"),
            "user_seen_at": conversation_data.get("user_seen_at"),
            "participants": participants,
            "page_name": conversation_data.get("page_name"),
            "page_avatar": conversation_data.get("page_avatar"),
            "page_category": conversation_data.get("page_category"),
            "user_info": conversation_data.get("user_info"),
            "ad_context": conversation_data.get("ad_context"),
        }

        schema = SocketConversationPayload(**payload)
        return schema.model_dump(mode="json")

    def build_message_payload(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build validated message payload for socket emissions.

        Args:
            message_data: Raw message data from database

        Returns:
            Validated and serialized message payload
        """
        # Parse template_data from JSON string if needed
        template_data = message_data.get("template_data")
        if template_data and isinstance(template_data, str):
            try:
                message_data["template_data"] = json.loads(template_data)
            except json.JSONDecodeError:
                message_data["template_data"] = None

        # Parse metadata from JSON string if needed (e.g. from some query paths)
        metadata_val = message_data.get("metadata")
        if metadata_val and isinstance(metadata_val, str):
            try:
                message_data["metadata"] = json.loads(metadata_val)
            except json.JSONDecodeError:
                message_data["metadata"] = None

        schema = SocketMessagePayload(**message_data)
        return schema.model_dump(mode="json")

    async def emit_message_event(
        self,
        page_admin_user_ids: List[str],
        conversation_data: Dict[str, Any],
        message_record: Dict[str, Any],
        metadata: Optional[str] = None,
    ) -> None:
        """
        Emit message event to all page admins via SocketService.

        Args:
            page_admin_user_ids: List of user IDs to notify
            conversation_data: Conversation data for payload
            message_record: Message data for payload
            metadata: Optional message metadata
        """
        if not self.socket_service or not page_admin_user_ids:
            return

        try:
            conversation_payload = self.build_conversation_payload(conversation_data)
            message_payload = self.build_message_payload(message_record)

            event_data = {
                "conversation": conversation_payload,
                "message": message_payload,
                "metadata": metadata,
            }

            for user_id in page_admin_user_ids:
                await self.socket_service.send_webhook_event(
                    user_id=user_id,
                    event_type="message_received",
                    event_data=event_data,
                )

        except Exception as e:
            logger.error(f"❌ Failed to emit message event: {e}")

    async def emit_read_receipt_event(
        self,
        page_admin_user_ids: List[str],
        conversation_data: Dict[str, Any],
        read_state: Dict[str, Any],
        watermark: int,
        timestamp: Optional[int] = None,
    ) -> None:
        """
        Emit read receipt event to all page admins.

        Args:
            page_admin_user_ids: List of user IDs to notify
            conversation_data: Conversation data for payload
            read_state: Read state data from database
            watermark: Facebook read watermark timestamp
            timestamp: Event timestamp
        """
        if not self.socket_service or not page_admin_user_ids:
            return

        try:
            conversation_payload = self.build_conversation_payload(conversation_data)

            event_data = {
                "conversation": conversation_payload,
                "read_receipt": {
                    "watermark": watermark,
                    "timestamp": timestamp,
                    "user_seen_at": read_state.get("user_seen_at"),
                },
            }

            for user_id in page_admin_user_ids:
                await self.socket_service.send_webhook_event(
                    user_id=user_id,
                    event_type="message_read",
                    event_data=event_data,
                )

        except Exception as e:
            logger.error(f"❌ Failed to emit read receipt event: {e}")
