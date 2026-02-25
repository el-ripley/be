"""
Facebook Webhook Handler - Entry point for webhook events.

This module receives raw webhook data from Facebook and routes events
to the appropriate handlers based on event type classification.

Flow:
    [Facebook Webhook] → [FbWebhookHandler]
          ↓
    classify_event() → "message" | "comment" | "post" | "unknown"
          ↓
    route to specific handler method
          ↓
    delegate to service layer
"""

from typing import Dict, Any, Optional, Callable, Awaitable

from src.services.facebook.comments.webhook_handler import CommentWebhookHandler
from src.services.facebook.messages.webhook_handler import MessageWebhookHandler
from src.utils.logger import get_logger

logger = get_logger()


# Type alias for messaging event handlers
MessagingEventHandler = Callable[[str, str, str, Dict[str, Any]], Awaitable[None]]


class FbWebhookHandler:
    """
    Main webhook handler that classifies and routes Facebook events.

    Supports:
    - Message events (messages, postbacks, read receipts)
    - Comment events (on posts)
    - Post events (status updates)
    """

    def __init__(
        self,
        comment_webhook_handler: CommentWebhookHandler,
        message_webhook_handler: MessageWebhookHandler,
    ):
        self.comment_webhook_handler = comment_webhook_handler
        self.message_webhook_handler = message_webhook_handler

        # Event category routing
        self._category_handlers: Dict[
            str, Callable[[Dict[str, Any]], Awaitable[None]]
        ] = {
            "message": self._handle_message_entry,
            "comment": self._handle_comment_entry,
            "post": self._handle_post_entry,
        }

        # Messaging event type routing
        self._messaging_handlers: Dict[str, MessagingEventHandler] = {
            "message": self._handle_message,
            "postback": self._handle_postback,
            "read": self._handle_read,
            "delivery": self._handle_delivery,
            "reaction": self._handle_reaction,
        }

    # ================================================================
    # PUBLIC API
    # ================================================================

    async def handle_fb_webhook_event(
        self,
        raw_data: Dict[str, Any],
        signature: str = None,
        headers: Dict[str, str] = None,
    ) -> None:
        """
        Process Facebook webhook event.

        Args:
            raw_data: Raw webhook data from Facebook
            signature: Webhook signature for verification
            headers: Request headers
        """
        try:
            await self._classify_and_route(raw_data)
        except Exception as e:
            logger.error(f"❌ Failed to process webhook event: {e}")
            logger.error(f"Raw data: {raw_data}")
            raise

    # ================================================================
    # EVENT CLASSIFICATION AND ROUTING
    # ================================================================

    async def _classify_and_route(self, raw_data: Dict[str, Any]) -> None:
        """Classify and route each entry to appropriate handler."""
        entries = raw_data.get("entry", [])

        if not entries:
            logger.warning("⚠️ No entries found in webhook data")
            return

        for entry in entries:
            category = self._classify_event(entry)
            handler = self._category_handlers.get(category)

            if handler:
                await handler(entry)
            else:
                await self._handle_unknown_entry(entry, category)

    def _classify_event(self, entry: Dict[str, Any]) -> str:
        """
        Classify a webhook entry into message, comment, post, or unknown.

        Args:
            entry: Single entry from webhook data

        Returns:
            Event category string
        """
        try:
            if "messaging" in entry:
                return "message"

            if "standby" in entry:
                return "message"

            if "changes" in entry:
                changes = entry.get("changes", [])
                if not changes:
                    return "unknown"

                change = changes[0]
                field = change.get("field", "")

                if field == "feed":
                    item = change.get("value", {}).get("item", "")
                    if item == "comment":
                        return "comment"
                    if item in ["status", "post", "photo", "video"]:
                        return "post"

                    logger.warning(f"⚠️ Unknown feed item type: {item}")
                    return "unknown"

                logger.warning(f"⚠️ Unknown field type: {field}")
                return "unknown"

            logger.warning("⚠️ No messaging or changes found in entry")
            return "unknown"

        except Exception as e:
            logger.error(f"❌ Failed to classify event: {e}")
            return "unknown"

    # ================================================================
    # CATEGORY HANDLERS
    # ================================================================

    async def _handle_message_entry(self, entry: Dict[str, Any]) -> None:
        """Route messaging events within an entry (including standby)."""
        page_id = entry.get("id")
        messaging_events = entry.get("messaging") or entry.get("standby", [])

        for event in messaging_events:
            await self._route_messaging_event(page_id, event)

    async def _handle_comment_entry(self, entry: Dict[str, Any]) -> None:
        """Handle comment events on posts."""
        try:
            page_id = entry.get("id")
            changes = entry.get("changes", [])

            for change in changes:
                value = change.get("value", {})
                verb = value.get("verb", "")

                await self.comment_webhook_handler.process_comment_event(
                    page_id=page_id,
                    comment_data=value,
                    verb=verb,
                )
        except Exception as e:
            logger.error(f"❌ Failed to handle comment event: {e}")

    async def _handle_post_entry(self, entry: Dict[str, Any]) -> None:
        """Handle post events (currently logging only)."""
        try:
            page_id = entry.get("id")
            changes = entry.get("changes", [])

            for change in changes:
                value = change.get("value", {})
                self._log_post_event(page_id, value)

        except Exception as e:
            logger.error(f"❌ Failed to handle post event: {e}")
            logger.error(f"Entry: {entry}")

    async def _handle_unknown_entry(self, entry: Dict[str, Any], category: str) -> None:
        """Handle unknown/unclassified events."""
        page_id = entry.get("id")
        logger.warning(
            f"❓ UNKNOWN EVENT | Category: {category} | Page: {page_id} | Keys: {list(entry.keys())}"
        )
        logger.debug(f"Unknown event entry: {entry}")

    # ================================================================
    # MESSAGING EVENT ROUTING
    # ================================================================

    async def _route_messaging_event(self, page_id: str, event: Dict[str, Any]) -> None:
        """Route a single messaging event to appropriate handler."""
        sender_id = event.get("sender", {}).get("id")
        recipient_id = event.get("recipient", {}).get("id")

        event_type = self._detect_messaging_event_type(event)

        if event_type:
            handler = self._messaging_handlers.get(event_type)
            if handler:
                try:
                    await handler(page_id, sender_id, recipient_id, event)
                except Exception as e:
                    logger.error(
                        f"❌ Failed to process {event_type} event | Page: {page_id} | "
                        f"Sender: {sender_id} | Error: {e}"
                    )
        else:
            logger.debug(
                f"❓ SKIPPED MESSAGE EVENT | Page: {page_id} | From: {sender_id} | "
                f"Keys: {list(event.keys())}"
            )

    def _detect_messaging_event_type(self, event: Dict[str, Any]) -> Optional[str]:
        """Detect the type of messaging event."""
        if "message_echoes" in event:
            return "message"  # Handle like message; payload has metadata in echo
        for event_type in ["message", "postback", "read", "delivery", "reaction"]:
            if event_type in event:
                return event_type
        return None

    # ================================================================
    # MESSAGING EVENT HANDLERS
    # ================================================================

    async def _handle_message(
        self,
        page_id: str,
        sender_id: str,
        recipient_id: str,
        event: Dict[str, Any],
    ) -> None:
        """Handle message event."""
        message = event["message"]
        is_echo = message.get("is_echo", False)
        timestamp = event.get("timestamp")
        timestamp_ms = self._convert_timestamp_to_milliseconds(timestamp)
        referral = event.get("referral") or message.get("referral")

        await self.message_webhook_handler.process_message_event(
            page_id=page_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            message_data=message,
            timestamp=timestamp_ms,
            is_echo=is_echo,
            referral=referral,
        )

    async def _handle_postback(
        self,
        page_id: str,
        sender_id: str,
        recipient_id: str,
        event: Dict[str, Any],
    ) -> None:
        """Handle postback event."""
        postback = event["postback"]
        timestamp = event.get("timestamp")
        # Convert Facebook webhook timestamp to milliseconds
        # Facebook webhook typically returns timestamp in seconds, but we handle both cases
        timestamp_ms = self._convert_timestamp_to_milliseconds(timestamp)

        await self.message_webhook_handler.process_postback_event(
            page_id=page_id,
            sender_id=sender_id,
            postback_data=postback,
            timestamp=timestamp_ms,
        )

    async def _handle_read(
        self,
        page_id: str,
        sender_id: str,
        recipient_id: str,
        event: Dict[str, Any],
    ) -> None:
        """Handle read receipt event."""
        read_payload = event["read"]
        watermark = read_payload.get("watermark")

        await self.message_webhook_handler.process_read_event(
            page_id=page_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            watermark=watermark,
            timestamp=event.get("timestamp"),
        )

    async def _handle_delivery(
        self,
        page_id: str,
        sender_id: str,
        recipient_id: str,
        event: Dict[str, Any],
    ) -> None:
        """Handle delivery event (currently no-op)."""
        pass

    async def _handle_reaction(
        self,
        page_id: str,
        sender_id: str,
        recipient_id: str,
        event: Dict[str, Any],
    ) -> None:
        """Handle reaction event (currently no-op)."""
        pass

    # ================================================================
    # HELPER METHODS
    # ================================================================

    @staticmethod
    def _convert_timestamp_to_milliseconds(timestamp: Optional[int]) -> Optional[int]:
        """
        Convert Facebook webhook timestamp to milliseconds.

        Facebook webhook typically returns timestamp in seconds (Unix timestamp),
        but we need to handle edge cases where it might already be in milliseconds.

        Logic:
        - If timestamp < 1e10 (10 billion), it's in seconds → convert to milliseconds
        - If timestamp >= 1e12 (1 trillion), it's already in milliseconds → return as-is
        - If 1e10 <= timestamp < 1e12, treat as seconds (very rare case for Facebook webhooks)

        Args:
            timestamp: Timestamp from Facebook webhook (seconds or milliseconds)

        Returns:
            Timestamp in milliseconds, or None if timestamp is None
        """
        if timestamp is None:
            return None

        try:
            # Unix timestamp in seconds for year 2024 is ~1.7 billion (1.7e9)
            # Unix timestamp in milliseconds for year 2001 is ~978 billion (9.78e11)
            # So 1e12 (1 trillion) is a safe threshold to distinguish seconds from milliseconds

            if timestamp >= 1e12:
                # Already in milliseconds
                return int(timestamp)
            else:
                # In seconds, convert to milliseconds
                return int(timestamp * 1000)
        except (TypeError, ValueError) as e:
            logger.warning(f"⚠️ Failed to convert timestamp {timestamp}: {e}")
            return None

    def _log_post_event(self, page_id: str, value: Dict[str, Any]) -> None:
        """Log post event details."""
        verb = value.get("verb", "")
        post_id = value.get("post_id", "")
        message = value.get("message", "")

        from_info = value.get("from", {})
        from_id = from_info.get("id", "")
        from_name = from_info.get("name", "")

        post_info = value.get("post", {})
        status_type = post_info.get("status_type", "")
        is_published = post_info.get("is_published", False)

        photos = value.get("photos", [])
        video_link = value.get("video", "")

        media_info = ""
        if photos:
            media_info = f" | Photos: {len(photos)}"
        elif video_link:
            media_info = f" | Video: {video_link}"

        text_preview = f"{message[:50]}..." if message else ""

        log_messages = {
            "add": f"📝 POST ADDED | Page: {page_id} | PostID: {post_id} | From: {from_name} ({from_id}) | Type: {status_type} | Published: {is_published} | Text: {text_preview}{media_info}",
            "edited": f"✏️ POST EDITED | Page: {page_id} | PostID: {post_id} | From: {from_name} ({from_id}) | Type: {status_type} | Text: {text_preview}{media_info}",
            "remove": f"🗑️ POST REMOVED | Page: {page_id} | PostID: {post_id} | From: {from_name} ({from_id})",
        }

        log_msg = log_messages.get(
            verb,
            f"❓ UNKNOWN POST ACTION | Page: {page_id} | Verb: {verb} | PostID: {post_id}",
        )
        logger.info(log_msg)

    def _parse_message_attachments(self, attachments: list) -> str:
        """Parse message attachments and return attachment info string."""
        if not attachments:
            return "No attachments"

        type_map = {
            "image": "Image",
            "photo": "Image",
            "video": "Video",
            "audio": "Audio",
            "file": "File",
            "template": "Template",
            "fallback": "Link",
        }

        attachment_types = [
            type_map.get(att.get("type", "unknown"), att.get("type", "unknown").title())
            for att in attachments
        ]

        return f"Attachments: {', '.join(attachment_types)}"
