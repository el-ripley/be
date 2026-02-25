"""
Read receipt processing service for Facebook webhook events.

Handles read receipt events and updates message read state.
"""

from typing import Dict, Any, List, Optional, TYPE_CHECKING

from src.database.postgres.repositories.facebook_queries import (
    mark_page_messages_seen_by_user,
)
from src.services.facebook.messages.sync import ConversationSyncService
from .socket_emitter import SocketEmitter
from src.services.facebook.auth import FacebookPageService
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.socket_service import SocketService

logger = get_logger()


class ReadReceiptProcessor:
    """Processes Facebook read receipt webhook events."""

    def __init__(
        self,
        page_service: FacebookPageService,
        page_scope_user_service: PageScopeUserService,
        socket_service: "SocketService",
    ):
        self.page_service = page_service
        self.page_scope_user_service = page_scope_user_service
        self.conversation_sync_service = ConversationSyncService(page_service)
        self.socket_emitter = SocketEmitter(socket_service)

    async def process_read_event(
        self,
        conn,
        page_id: str,
        sender_id: str,
        watermark: int,
        timestamp: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Process a Facebook read receipt event (user viewed page messages).

        Args:
            conn: Database connection
            page_id: Facebook page ID
            sender_id: User who read the messages (PSID)
            watermark: Facebook watermark timestamp
            timestamp: Event timestamp

        Returns:
            Result dict with conversation and read_state, or None if no updates
        """
        # Validate watermark
        if not watermark:
            logger.debug(
                "Skipping read event with missing watermark | Page: %s | Sender: %s",
                page_id,
                sender_id,
            )
            return None

        if not sender_id:
            logger.warning(
                "Skipping read event with missing sender_id | Page: %s",
                page_id,
            )
            return None

        try:
            watermark_value = int(watermark)
        except (TypeError, ValueError):
            logger.warning(
                "Skipping read event with invalid watermark | Page: %s | Sender: %s | Watermark: %s",
                page_id,
                sender_id,
                watermark,
            )
            return None

        # Ensure page scope user exists before creating conversation
        page_admins = await self.page_service.get_facebook_page_admins_by_page_id(
            conn, page_id
        )
        await self.page_scope_user_service.get_or_create_page_scope_user(
            conn=conn,
            psid=sender_id,
            page_id=page_id,
            page_admins=page_admins,
        )

        # Ensure conversation exists
        conversation_data = await self.conversation_sync_service.ensure_conversation(
            conn=conn,
            page_id=page_id,
            user_psid=sender_id,
            page_admins=page_admins,
        )

        conversation_id = conversation_data["conversation_id"]

        # Mark messages as seen
        read_state = await mark_page_messages_seen_by_user(
            conn=conn,
            conversation_id=conversation_id,
            watermark=watermark_value,
        )

        if not read_state:
            logger.info(
                "Read event had no matching messages | Page: %s | User: %s | Watermark: %s",
                page_id,
                sender_id,
                watermark,
            )
            return None

        # Update conversation data with read state
        conversation_data["user_seen_at"] = read_state.get("user_seen_at")
        conversation_data["conversation_updated_at"] = read_state.get("updated_at")

        # Note: mark_as_read is now a separate UX field in the database,
        # not computed from unread_count. The actual value comes from conversation_data.

        return {
            "conversation": conversation_data,
            "read_state": read_state,
            "watermark": watermark_value,
            "timestamp": timestamp,
        }

    async def emit_read_receipt_event(
        self,
        page_admin_user_ids: List[str],
        result: Dict[str, Any],
    ) -> None:
        """
        Emit socket event for processed read receipt.

        Args:
            page_admin_user_ids: List of user IDs to notify
            result: Processing result from process_read_event
        """
        await self.socket_emitter.emit_read_receipt_event(
            page_admin_user_ids=page_admin_user_ids,
            conversation_data=result["conversation"],
            read_state=result["read_state"],
            watermark=result["watermark"],
            timestamp=result.get("timestamp"),
        )
