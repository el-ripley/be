"""
Message processing service for Facebook webhook events.

Handles message and postback event processing, including database storage
and socket emissions.
"""

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.database.postgres.repositories.facebook_queries import (
    create_message,
    get_conversation_by_participants,
    update_conversation_ad_context,
    update_conversation_after_message,
)
from src.services.facebook.auth import FacebookPageService
from src.services.facebook.messages.sync import ConversationSyncService
from src.services.facebook.posts.post_sync_service import PostSyncService
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.utils.logger import get_logger

from .attachment_parser import build_entry_point, merge_entry_point, parse_attachments
from .socket_emitter import SocketEmitter

if TYPE_CHECKING:
    from src.socket_service import SocketService

logger = get_logger()


class MessageProcessor:
    """Processes Facebook message and postback webhook events."""

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
        self.post_sync_service = PostSyncService(page_service)

    async def process_message(
        self,
        conn,
        page_id: str,
        sender_id: str,
        recipient_id: str,
        message_data: Dict[str, Any],
        timestamp: int,
        is_echo: bool = False,
        referral: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process a Facebook message event.

        Args:
            conn: Database connection
            page_id: Facebook page ID
            sender_id: Message sender ID (PSID or page ID)
            recipient_id: Message recipient ID (PSID or page ID)
            message_data: Message data from webhook
            timestamp: Facebook timestamp
            is_echo: Whether this is an echo message (sent by page)
            referral: Optional referral data

        Returns:
            Dictionary with conversation and message data
        """
        # Extract message fields
        message_id = message_data.get("mid", "")
        text = message_data.get("text", "")
        attachments = message_data.get("attachments", [])
        metadata_raw = message_data.get("metadata")
        referral = referral or message_data.get("referral")
        reply_to = message_data.get("reply_to")
        reply_to_message_id = (
            reply_to.get("mid") if isinstance(reply_to, dict) and reply_to else None
        )

        # Parse metadata for persistence (e.g. {"sent_by": "ai_agent", "history_id": "..."} from echo)
        message_metadata: Optional[Dict[str, Any]] = None
        if metadata_raw and isinstance(metadata_raw, str):
            try:
                parsed = json.loads(metadata_raw)
                if isinstance(parsed, dict) and parsed.get("sent_by"):
                    message_metadata = parsed
            except (json.JSONDecodeError, TypeError):
                pass

        # Parse attachments and entry point
        attachment_urls = parse_attachments(attachments)
        entry_point = build_entry_point(referral)

        # Determine user ID based on echo status
        user_id = recipient_id if is_echo else sender_id

        # Ensure page scope user exists
        page_admins = await self.page_service.get_facebook_page_admins_by_page_id(
            conn, page_id
        )
        await self.page_scope_user_service.get_or_create_page_scope_user(
            conn=conn,
            psid=user_id,
            page_id=page_id,
            page_admins=page_admins,
        )

        # Get or create conversation
        conversation_data = await self.conversation_sync_service.ensure_conversation(
            conn=conn,
            page_id=page_id,
            user_psid=user_id,
            page_admins=page_admins,
        )
        conversation_id = conversation_data["conversation_id"]

        # Create message record
        template_payload = merge_entry_point(
            attachment_urls.get("template"), entry_point
        )

        message_record = await create_message(
            conn=conn,
            message_id=message_id,
            conversation_id=conversation_id,
            is_echo=is_echo,
            text=text,
            photo_url=attachment_urls.get("photo"),
            video_url=attachment_urls.get("video"),
            audio_url=attachment_urls.get("audio"),
            template_data=template_payload,
            facebook_timestamp=timestamp,
            metadata=message_metadata,
            reply_to_message_id=reply_to_message_id,
        )

        # Save ad_context to conversation if referral has ads_context_data
        if referral and referral.get("ads_context_data"):
            ads_context_data = referral.get("ads_context_data", {})
            ad_context = {
                "ad_id": referral.get("ad_id"),
                "source": referral.get("source"),
                "type": referral.get("type"),
                **ads_context_data,
            }

            # Sync post if post_id is present in ads_context_data
            post_id = ads_context_data.get("post_id")
            if post_id:
                try:
                    await self.post_sync_service.get_or_create_post(
                        conn=conn,
                        post_id=post_id,
                        page_id=page_id,
                        page_admins=page_admins,
                        context_data=message_data,  # Use message_data for logging context
                    )
                    logger.info(
                        f"📢 Synced post {post_id} from referral for conversation {conversation_id}"
                    )
                except Exception as e:
                    logger.error(f"❌ Failed to sync post {post_id} from referral: {e}")
                    # Continue even if post sync fails

            await update_conversation_ad_context(conn, conversation_id, ad_context)
            logger.info(
                f"📢 Saved ad context to conversation {conversation_id}: ad_id={ad_context.get('ad_id')}"
            )

        # Update conversation metadata
        await update_conversation_after_message(
            conn=conn,
            conversation_id=conversation_id,
            message_id=message_record["id"],
            is_echo=is_echo,
            facebook_timestamp=timestamp,
        )

        # Re-fetch conversation for updated counts
        # Preserve internal flags from original conversation_data
        original_is_new = conversation_data.get("_is_new", False)
        original_sync_history = conversation_data.get("_sync_history", False)
        original_conversation_id = conversation_data.get("_conversation_id")
        original_page_id = conversation_data.get("_page_id")
        original_page_admins = conversation_data.get("_page_admins")

        conversation_data = await get_conversation_by_participants(
            conn=conn,
            fan_page_id=page_id,
            facebook_page_scope_user_id=user_id,
        )

        # Restore internal flags if conversation was new
        if original_is_new:
            conversation_data["_is_new"] = original_is_new
            conversation_data["_sync_history"] = original_sync_history
            conversation_data["_conversation_id"] = original_conversation_id
            conversation_data["_page_id"] = original_page_id
            conversation_data["_page_admins"] = original_page_admins

        return {
            "conversation": conversation_data,
            "message": message_record,
            "metadata": metadata_raw,
        }

    async def process_postback(
        self,
        conn,
        page_id: str,
        sender_id: str,
        postback_data: Dict[str, Any],
        timestamp: int,
    ) -> Dict[str, Any]:
        """
        Process a Facebook postback event.

        Args:
            conn: Database connection
            page_id: Facebook page ID
            sender_id: Postback sender ID (PSID)
            postback_data: Postback data from webhook
            timestamp: Facebook timestamp

        Returns:
            Dictionary with conversation and message data
        """
        payload = postback_data.get("payload", "")
        title = postback_data.get("title", "")
        mid = postback_data.get("mid", "")

        # Create synthetic message ID if not provided
        if not mid:
            mid = f"postback_{timestamp}_{sender_id}"

        # Build template data for postback
        template_data: Dict[str, Any] = {
            "type": "postback",
            "payload": payload,
            "title": title,
            "original_data": postback_data,
        }

        entry_point = build_entry_point(postback_data.get("referral"))
        if entry_point:
            template_data["entry_point"] = entry_point

        # Ensure page scope user exists
        page_admins = await self.page_service.get_facebook_page_admins_by_page_id(
            conn, page_id
        )
        await self.page_scope_user_service.get_or_create_page_scope_user(
            conn=conn,
            psid=sender_id,
            page_id=page_id,
            page_admins=page_admins,
        )

        # Get or create conversation
        conversation_data = await self.conversation_sync_service.ensure_conversation(
            conn=conn,
            page_id=page_id,
            user_psid=sender_id,
            page_admins=page_admins,
        )
        conversation_id = conversation_data["conversation_id"]

        # Create message record for postback
        message_record = await create_message(
            conn=conn,
            message_id=mid,
            conversation_id=conversation_id,
            is_echo=False,
            text=f"Postback: {title}",
            template_data=template_data,
            facebook_timestamp=timestamp,
        )

        # Save ad_context to conversation if postback has referral with ads_context_data
        referral = postback_data.get("referral")
        if referral and referral.get("ads_context_data"):
            ads_context_data = referral.get("ads_context_data", {})
            ad_context = {
                "ad_id": referral.get("ad_id"),
                "source": referral.get("source"),
                "type": referral.get("type"),
                **ads_context_data,
            }

            # Sync post if post_id is present in ads_context_data
            post_id = ads_context_data.get("post_id")
            if post_id:
                try:
                    await self.post_sync_service.get_or_create_post(
                        conn=conn,
                        post_id=post_id,
                        page_id=page_id,
                        page_admins=page_admins,
                        context_data=postback_data,  # Use postback_data for logging context
                    )
                    logger.info(
                        f"📢 Synced post {post_id} from referral (postback) for conversation {conversation_id}"
                    )
                except Exception as e:
                    logger.error(
                        f"❌ Failed to sync post {post_id} from referral (postback): {e}"
                    )
                    # Continue even if post sync fails

            await update_conversation_ad_context(conn, conversation_id, ad_context)
            logger.info(
                f"📢 Saved ad context (from postback) to conversation {conversation_id}: ad_id={ad_context.get('ad_id')}"
            )

        # Update conversation metadata
        await update_conversation_after_message(
            conn=conn,
            conversation_id=conversation_id,
            message_id=message_record["id"],
            is_echo=False,
            facebook_timestamp=timestamp,
        )

        # Re-fetch conversation for updated counts
        # Preserve internal flags from original conversation_data
        original_is_new = conversation_data.get("_is_new", False)
        original_sync_history = conversation_data.get("_sync_history", False)
        original_conversation_id = conversation_data.get("_conversation_id")
        original_page_id = conversation_data.get("_page_id")
        original_page_admins = conversation_data.get("_page_admins")

        conversation_data = await get_conversation_by_participants(
            conn=conn,
            fan_page_id=page_id,
            facebook_page_scope_user_id=sender_id,
        )

        # Restore internal flags if conversation was new
        if original_is_new:
            conversation_data["_is_new"] = original_is_new
            conversation_data["_sync_history"] = original_sync_history
            conversation_data["_conversation_id"] = original_conversation_id
            conversation_data["_page_id"] = original_page_id
            conversation_data["_page_admins"] = original_page_admins

        return {
            "conversation": conversation_data,
            "message": message_record,
        }

    async def emit_message_event(
        self,
        page_admin_user_ids: List[str],
        result: Dict[str, Any],
    ) -> None:
        """
        Emit socket event for processed message/postback.

        Args:
            page_admin_user_ids: List of user IDs to notify
            result: Processing result from process_message/process_postback
        """
        await self.socket_emitter.emit_message_event(
            page_admin_user_ids=page_admin_user_ids,
            conversation_data=result["conversation"],
            message_record=result["message"],
            metadata=result.get("metadata"),
        )
