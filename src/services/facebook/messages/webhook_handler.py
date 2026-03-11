"""
Facebook message webhook handler - Orchestrator.

This module serves as a thin orchestration layer that coordinates
the various processors for handling Facebook webhook events.

Flow:
    [Webhook Entry]
          ↓
    [FbWebhookHandler] → classify event → route
          ↓
    [MessageWebhookHandler] → orchestrate processing
          ↓
    ┌─────────────────────────────────────────────────┐
    │  MessageProcessor    │  ReadReceiptProcessor    │
    │  ConversationSyncService │  SocketEmitter           │
    └─────────────────────────────────────────────────┘
"""

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories import get_page_admin_suggest_configs_by_page
from src.services.facebook.auth import FacebookPageService
from src.services.facebook.messages._internal.message_processor import MessageProcessor
from src.services.facebook.messages._internal.read_receipt_processor import (
    ReadReceiptProcessor,
)
from src.services.facebook.messages.sync import (
    ConversationMessageHistorySync,
    ConversationSyncService,
)
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.socket_service import SocketService

logger = get_logger()


class MessageWebhookHandler:
    """
    Orchestrator for processing Facebook message webhook events.

    Delegates actual processing to specialized processors:
    - MessageProcessor: Handles message and postback events
    - ReadReceiptProcessor: Handles read receipt events
    - ConversationSyncService: Manages realtime conversation sync
    """

    def __init__(
        self,
        page_service: FacebookPageService,
        page_scope_user_service: PageScopeUserService,
        socket_service: "SocketService",
        suggest_response_orchestrator: Optional[Any] = None,
    ):
        self.socket_service = socket_service
        self._page_service = page_service
        self._suggest_response_orchestrator = suggest_response_orchestrator
        self._message_history_sync = ConversationMessageHistorySync(page_service)

        # Initialize processors
        self._message_processor = MessageProcessor(
            page_service=page_service,
            page_scope_user_service=page_scope_user_service,
            socket_service=socket_service,
        )
        self._read_receipt_processor = ReadReceiptProcessor(
            page_service=page_service,
            page_scope_user_service=page_scope_user_service,
            socket_service=socket_service,
        )
        self._conversation_sync_service = ConversationSyncService(page_service)

    async def _get_page_admin_user_ids(self, conn, page_id: str) -> List[str]:
        """
        Get all user IDs of page admins for socket emissions.

        Args:
            conn: Database connection
            page_id: Facebook page ID

        Returns:
            List of user IDs
        """
        try:
            page_admins = await self._page_service.get_facebook_page_admins_by_page_id(
                conn, page_id
            )
            return [
                admin.get("user_id") for admin in page_admins if admin.get("user_id")
            ]
        except Exception as e:
            logger.error(f"❌ Failed to get page admin user_ids for page {page_id}: {e}")
            return []

    async def process_message_event(
        self,
        page_id: str,
        sender_id: str,
        recipient_id: str,
        message_data: Dict[str, Any],
        timestamp: int,
        is_echo: bool = False,
        referral: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Process a Facebook message event.

        Args:
            page_id: Facebook page ID
            sender_id: Message sender ID (PSID or page ID)
            recipient_id: Message recipient ID (PSID or page ID)
            message_data: Message data from webhook
            timestamp: Facebook timestamp
            is_echo: Whether this is an echo message (sent by page)
            referral: Optional referral data
        """
        try:
            conversation_was_new = False
            sync_info = None

            async with async_db_transaction() as conn:
                # Process message
                result = await self._message_processor.process_message(
                    conn=conn,
                    page_id=page_id,
                    sender_id=sender_id,
                    recipient_id=recipient_id,
                    message_data=message_data,
                    timestamp=timestamp,
                    is_echo=is_echo,
                    referral=referral,
                )

                # Check if conversation was newly created and needs sync
                conversation_data = result.get("conversation", {})
                if conversation_data.get("_is_new") and conversation_data.get(
                    "_sync_history"
                ):
                    conversation_was_new = True
                    sync_info = {
                        "conversation_id": conversation_data.get("_conversation_id"),
                        "page_id": conversation_data.get("_page_id"),
                        "page_admins": conversation_data.get("_page_admins"),
                    }
                    # Clean up internal flags before emitting
                    for key in [
                        "_is_new",
                        "_sync_history",
                        "_conversation_id",
                        "_page_id",
                        "_page_admins",
                    ]:
                        conversation_data.pop(key, None)

                # Get page admins and emit socket event
                page_admin_user_ids = await self._get_page_admin_user_ids(conn, page_id)

            # After transaction commits, sync history if needed
            if conversation_was_new and sync_info:
                await self._message_history_sync.sync_history_after_commit(
                    conversation_id=sync_info["conversation_id"],
                    page_id=sync_info["page_id"],
                    page_admins=sync_info.get("page_admins"),
                    max_messages=100,
                )

            # Emit socket event after sync completes
            if self.socket_service and page_admin_user_ids:
                await self._message_processor.emit_message_event(
                    page_admin_user_ids=page_admin_user_ids,
                    result=result,
                )

            # Trigger suggest response for admins with webhook automation enabled
            # Run in background so webhook returns 200 quickly and socket emits can flush to FE
            if (
                not is_echo
                and self._suggest_response_orchestrator
                and result.get("conversation")
            ):
                conversation_data = result["conversation"]
                conversation_id = conversation_data.get("conversation_id")
                if conversation_id:
                    asyncio.create_task(
                        self._run_suggest_response_webhook_background(
                            page_id=page_id,
                            conversation_id=conversation_id,
                            facebook_page_scope_user_id=sender_id,
                        )
                    )

        except Exception as e:
            logger.error(f"❌ Failed to process message event: {e}")
            logger.error(f"Message data: {message_data}")
            raise

    async def _run_suggest_response_webhook_background(
        self,
        page_id: str,
        conversation_id: str,
        facebook_page_scope_user_id: str,
    ) -> None:
        """Run suggest response for webhook in background so socket emits can flush to FE."""
        try:
            await self._trigger_suggest_response_for_webhook(
                page_id=page_id,
                conversation_id=conversation_id,
                facebook_page_scope_user_id=facebook_page_scope_user_id,
            )
        except Exception as e:
            logger.error(
                f"Suggest response webhook background task failed: {e}",
                exc_info=True,
            )

    async def _trigger_suggest_response_for_webhook(
        self,
        page_id: str,
        conversation_id: str,
        facebook_page_scope_user_id: str,
    ) -> None:
        """Trigger suggest response for admins with webhook automation enabled."""
        if not self._suggest_response_orchestrator:
            return
        try:
            from src.database.postgres.connection import async_db_transaction

            async with async_db_transaction() as conn:
                page_admins_with_config = await get_page_admin_suggest_configs_by_page(
                    conn, page_id
                )
                for admin in page_admins_with_config:
                    if not admin.get("auto_webhook_suggest") and not admin.get(
                        "auto_webhook_graph_api"
                    ):
                        continue
                    user_id = admin.get("user_id")
                    page_admin_id = admin.get("page_admin_id")
                    if not user_id or not page_admin_id:
                        continue
                    # Build page_admin dict for Graph API delivery
                    page_admin = {
                        "id": page_admin_id,
                        "page_id": admin.get("page_id"),
                        "access_token": admin.get("access_token"),
                    }
                    try:
                        await self._suggest_response_orchestrator.trigger(
                            user_id=user_id,
                            conversation_type="messages",
                            conversation_id=conversation_id,
                            fan_page_id=page_id,
                            trigger_source="webhook",
                            page_admin_id=page_admin_id,
                            page_admin=page_admin,
                            facebook_page_scope_user_id=facebook_page_scope_user_id,
                            webhook_delay_seconds=admin.get("webhook_delay_seconds", 5),
                        )
                    except Exception as e:
                        logger.warning(
                            f"Suggest response webhook trigger failed for admin {user_id}: {e}"
                        )
        except Exception as e:
            logger.error(f"Failed to trigger suggest response for webhook: {e}")

    async def process_postback_event(
        self,
        page_id: str,
        sender_id: str,
        postback_data: Dict[str, Any],
        timestamp: int,
    ) -> None:
        """
        Process a Facebook postback event.

        Args:
            page_id: Facebook page ID
            sender_id: Postback sender ID (PSID)
            postback_data: Postback data from webhook
            timestamp: Facebook timestamp
        """
        try:
            conversation_was_new = False
            sync_info = None

            async with async_db_transaction() as conn:
                # Process postback
                result = await self._message_processor.process_postback(
                    conn=conn,
                    page_id=page_id,
                    sender_id=sender_id,
                    postback_data=postback_data,
                    timestamp=timestamp,
                )

                # Check if conversation was newly created and needs sync
                conversation_data = result.get("conversation", {})
                if conversation_data.get("_is_new") and conversation_data.get(
                    "_sync_history"
                ):
                    conversation_was_new = True
                    sync_info = {
                        "conversation_id": conversation_data.get("_conversation_id"),
                        "page_id": conversation_data.get("_page_id"),
                        "page_admins": conversation_data.get("_page_admins"),
                    }
                    # Clean up internal flags before emitting
                    for key in [
                        "_is_new",
                        "_sync_history",
                        "_conversation_id",
                        "_page_id",
                        "_page_admins",
                    ]:
                        conversation_data.pop(key, None)

                # Get page admins and emit socket event
                page_admin_user_ids = await self._get_page_admin_user_ids(conn, page_id)

            # After transaction commits, sync history if needed
            if conversation_was_new and sync_info:
                await self._message_history_sync.sync_history_after_commit(
                    conversation_id=sync_info["conversation_id"],
                    page_id=sync_info["page_id"],
                    page_admins=sync_info.get("page_admins"),
                    max_messages=100,
                )

            # Emit socket event after sync completes
            if self.socket_service and page_admin_user_ids:
                await self._message_processor.emit_message_event(
                    page_admin_user_ids=page_admin_user_ids,
                    result=result,
                )

            # Trigger suggest response for admins with webhook automation enabled
            if self._suggest_response_orchestrator and result.get("conversation"):
                conversation_data = result["conversation"]
                conversation_id = conversation_data.get("conversation_id")
                if conversation_id:
                    asyncio.create_task(
                        self._run_suggest_response_webhook_background(
                            page_id=page_id,
                            conversation_id=conversation_id,
                            facebook_page_scope_user_id=sender_id,
                        )
                    )

        except Exception as e:
            logger.error(f"❌ Failed to process postback event: {e}")
            logger.error(f"Postback data: {postback_data}")
            raise

    async def process_read_event(
        self,
        page_id: str,
        sender_id: str,
        recipient_id: str,
        watermark: int,
        timestamp: Optional[int] = None,
    ) -> None:
        """
        Process a Facebook read receipt event.

        Args:
            page_id: Facebook page ID
            sender_id: User who read the messages (PSID)
            recipient_id: Recipient ID (page ID)
            watermark: Facebook watermark timestamp
            timestamp: Event timestamp
        """
        try:
            async with async_db_transaction() as conn:
                # Process read receipt
                result = await self._read_receipt_processor.process_read_event(
                    conn=conn,
                    page_id=page_id,
                    sender_id=sender_id,
                    watermark=watermark,
                    timestamp=timestamp,
                )

                if not result:
                    return  # No updates needed

                # Get page admins and emit socket event
                page_admin_user_ids = await self._get_page_admin_user_ids(conn, page_id)

                if self.socket_service and page_admin_user_ids:
                    await self._read_receipt_processor.emit_read_receipt_event(
                        page_admin_user_ids=page_admin_user_ids,
                        result=result,
                    )

        except Exception as e:
            logger.error(f"❌ Failed to process read event: {e}")
            raise
