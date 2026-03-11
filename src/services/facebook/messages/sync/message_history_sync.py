"""
Conversation message history synchronization service.

Handles fetching and syncing historical messages from Facebook Graph API
for a specific conversation.
"""

import asyncio
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from src.common.clients.facebook_graph_page_client import FacebookGraphPageClient
from src.database.postgres.connection import get_async_connection
from src.database.postgres.repositories.facebook_queries import (
    batch_create_messages,
    create_message,
)
from src.database.postgres.repositories.facebook_queries.messages.conversations import (
    refresh_conversation_latest_message,
)
from src.services.facebook._core.helpers import execute_graph_client_with_random_tokens
from src.services.facebook.auth import FacebookPageService
from src.services.facebook.messages._internal.attachment_parser import parse_attachments
from src.utils.logger import get_logger

logger = get_logger()


def extract_story_fbid_from_text(text: str) -> Optional[str]:
    """
    Extract story_fbid from old Facebook message format.

    Old messages that replied to ads contained URLs with story_fbid parameter.
    Example text: "Xem bài viết(https://www.facebook.com/story.php?story_fbid=563970067279076&id=198202053855881)"

    Args:
        text: Message text content

    Returns:
        story_fbid string if found, None otherwise
    """
    if not text:
        return None

    # Match story_fbid parameter in URL
    match = re.search(r"story_fbid=(\d+)", text)
    if match:
        return match.group(1)

    return None


class ConversationMessageHistorySync:
    """Syncs message history from Facebook to local database for a conversation."""

    def __init__(self, page_service: FacebookPageService):
        self.page_service = page_service

    async def sync_history_after_commit(
        self,
        conversation_id: str,
        page_id: str,
        page_admins: Optional[List[Dict[str, Any]]] = None,
        max_messages: int = 100,
    ) -> int:
        """
        Sync message history after transaction commits.

        This should be called after the transaction that created the conversation
        has committed, to ensure the conversation is visible to sync's separate connections.

        Args:
            conversation_id: Facebook conversation ID
            page_id: Facebook page ID
            page_admins: Optional list of page admin records with access tokens
            max_messages: Maximum number of messages to sync (default: 100 for webhook flow)

        Returns:
            Number of messages synced
        """
        # Small delay to ensure transaction has committed
        await asyncio.sleep(0.1)

        try:
            async with get_async_connection() as conn:
                # Re-fetch page_admins if not provided (they may not be available after transaction)
                if page_admins is None:
                    page_admins = (
                        await self.page_service.get_facebook_page_admins_by_page_id(
                            conn, page_id
                        )
                    )

                synced_count = await self.sync_conversation_history(
                    conn=conn,
                    conversation_id=conversation_id,
                    page_id=page_id,
                    page_admins=page_admins,
                    max_messages=max_messages,
                )
                logger.info(
                    f"✅ Synced {synced_count} historical messages for conversation {conversation_id}"
                )
                return synced_count
        except Exception as e:
            logger.warning(
                f"⚠️ Failed to sync message history for conversation {conversation_id}: {e}"
            )
            return 0

    async def sync_conversation_history(
        self,
        conn,
        conversation_id: str,
        page_id: str,
        page_admins: Optional[List[Dict[str, Any]]] = None,
        max_messages: int = 250,  # Increased from 100 for better coverage (most conversations < 250 messages)
        max_concurrent: int = 10,  # Optimized: Lower concurrency prevents connection pool exhaustion
    ) -> int:
        """
        Sync message history from Facebook to local database.

        OPTIMIZATION: Uses batch insert instead of individual inserts for much better performance.

        Args:
            conn: Database connection
            conversation_id: Facebook conversation ID
            page_id: Facebook page ID
            page_admins: List of page admin records with access tokens
            max_messages: Maximum number of messages to sync
            max_concurrent: Not used anymore (kept for backward compatibility)

        Returns:
            Number of messages synced
        """
        admins = page_admins
        if admins is None:
            admins = await self.page_service.get_facebook_page_admins_by_page_id(
                conn, page_id
            )

        async def callback(client: FacebookGraphPageClient) -> Optional[Dict[str, Any]]:
            return await client.get_full_conversation_history(
                conversation_id=conversation_id,
                messages_page_size=100,  # Increased from 50 to max (100) to reduce API calls
                max_messages=max_messages,
            )

        history = await execute_graph_client_with_random_tokens(
            page_admins=admins,
            callback=callback,
            operation_name=f"get message history for conversation {conversation_id}",
        )

        if not history or not history.get("messages"):
            logger.info(
                f"No message history to sync for conversation {conversation_id}"
            )
            return 0

        messages = history.get("messages", [])

        # NEW APPROACH: Parse all messages into batch data, then insert in ONE operation
        # This is MUCH faster than individual inserts (250 individual INSERTs → 1 batch INSERT)
        messages_batch_data = []

        for fb_message in messages:
            message_data = self._parse_message_for_batch(
                conversation_id=conversation_id,
                page_id=page_id,
                fb_message=fb_message,
            )
            if message_data:
                messages_batch_data.append(message_data)

        # Batch insert all messages at once
        if messages_batch_data:
            try:
                await batch_create_messages(conn, messages_batch_data)
                synced_count = len(messages_batch_data)
                logger.info(
                    f"✅ Batch inserted {synced_count} messages for conversation {conversation_id}"
                )
            except Exception as e:
                logger.error(
                    f"❌ Failed to batch insert messages for conversation {conversation_id}: {e}"
                )
                # Fallback to individual inserts if batch fails
                logger.warning("⚠️ Falling back to individual message inserts")
                synced_count = 0
                for message_data in messages_batch_data:
                    try:
                        await create_message(
                            conn=conn,
                            message_id=message_data["message_id"],
                            conversation_id=message_data["conversation_id"],
                            is_echo=message_data["is_echo"],
                            text=message_data["text"],
                            photo_url=message_data.get("photo_url"),
                            video_url=message_data.get("video_url"),
                            audio_url=message_data.get("audio_url"),
                            template_data=message_data.get("template_data"),
                            facebook_timestamp=message_data.get("facebook_timestamp"),
                            reply_to_message_id=message_data.get("reply_to_message_id"),
                        )
                        synced_count += 1
                    except Exception as fallback_e:
                        logger.warning(
                            f"⚠️ Failed to insert message {message_data['message_id']}: {fallback_e}"
                        )
        else:
            synced_count = 0

        # After syncing all messages, refresh the latest_message metadata on conversation
        # This ensures latest_message_* fields are accurate after bulk sync operations
        try:
            await refresh_conversation_latest_message(conn, conversation_id)
            logger.debug(
                f"✅ Refreshed latest_message metadata for conversation {conversation_id}"
            )
        except Exception as e:
            logger.warning(
                f"⚠️ Failed to refresh latest_message for conversation {conversation_id}: {e}"
            )
            # Don't fail the entire sync if this update fails

        logger.info(
            f"✅ Synced {synced_count}/{len(messages)} messages for conversation {conversation_id}"
        )
        return synced_count

    def _parse_message_for_batch(
        self,
        conversation_id: str,
        page_id: str,
        fb_message: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Parse Facebook message data into batch insert format.

        Args:
            conversation_id: Local conversation ID
            page_id: Facebook page ID
            fb_message: Facebook message data

        Returns:
            Message data dict for batch insert or None if invalid
        """
        message_id = fb_message.get("id")
        if not message_id:
            return None

        # Determine if this is an echo (sent by page)
        sender = fb_message.get("from", {})
        sender_id = sender.get("id", "")
        is_echo = sender_id == page_id

        # Parse message content
        text = fb_message.get("message", "")

        # Parse attachments
        attachments_raw = fb_message.get("attachments")
        if isinstance(attachments_raw, dict):
            attachments = attachments_raw.get("data", [])
        elif isinstance(attachments_raw, list):
            attachments = attachments_raw
        else:
            attachments = []

        attachment_urls = parse_attachments(attachments)

        # Parse sticker as template data
        sticker = fb_message.get("sticker")
        template_data = attachment_urls.get("template")
        if sticker and not template_data:
            template_data = {"type": "sticker", "sticker": sticker}

        # Parse shares as template data
        shares = fb_message.get("shares", {}).get("data", [])
        if shares and not template_data:
            template_data = {"type": "shares", "shares": shares}

        # Try to extract story_fbid from old message format (for ad replies)
        # Old format: "Xem bài viết(https://www.facebook.com/story.php?story_fbid=XXX&id=YYY)"
        story_fbid = extract_story_fbid_from_text(text)
        if story_fbid:
            if not template_data:
                template_data = {}
            # Store extracted story info
            if "entry_point" not in template_data:
                template_data["entry_point"] = {
                    "source": "EXTRACTED_FROM_TEXT",
                    "post_id": story_fbid,
                }
            logger.debug(
                f"📝 Extracted story_fbid {story_fbid} from message {message_id}"
            )

        # Convert Facebook created_time to timestamp
        facebook_timestamp = self._parse_facebook_timestamp(
            fb_message.get("created_time")
        )

        return {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "is_echo": is_echo,
            "text": text,
            "photo_url": attachment_urls.get("photo"),
            "video_url": attachment_urls.get("video"),
            "audio_url": attachment_urls.get("audio"),
            "template_data": template_data,
            "facebook_timestamp": facebook_timestamp,
            "reply_to_message_id": None,  # Graph API does not expose reply_to when reading messages
        }

    async def _create_message_from_facebook(
        self,
        conn,
        conversation_id: str,
        page_id: str,
        fb_message: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Create a message record from Facebook message data.

        DEPRECATED: Use batch insert via _parse_message_for_batch instead for better performance.
        This method is kept for backward compatibility and fallback scenarios.

        Args:
            conn: Database connection
            conversation_id: Local conversation ID
            page_id: Facebook page ID
            fb_message: Facebook message data

        Returns:
            Created message record or None if skipped
        """
        message_data = self._parse_message_for_batch(
            conversation_id=conversation_id,
            page_id=page_id,
            fb_message=fb_message,
        )

        if not message_data:
            return None

        return await create_message(
            conn=conn,
            message_id=message_data["message_id"],
            conversation_id=message_data["conversation_id"],
            is_echo=message_data["is_echo"],
            text=message_data["text"],
            photo_url=message_data.get("photo_url"),
            video_url=message_data.get("video_url"),
            audio_url=message_data.get("audio_url"),
            template_data=message_data.get("template_data"),
            facebook_timestamp=message_data.get("facebook_timestamp"),
            reply_to_message_id=message_data.get("reply_to_message_id"),
        )

    @staticmethod
    def _parse_facebook_timestamp(
        created_time: Optional[Union[str, int, float]],
    ) -> Optional[int]:
        """
        Convert Facebook timestamp to Unix timestamp in milliseconds.

        Handles multiple formats:
        - ISO string: "2024-01-15T10:30:00+0000" or "2024-01-15T10:30:00Z"
        - Unix timestamp (seconds): 1705312200
        - Unix timestamp (milliseconds): 1705312200000 (will be returned as-is)
        """
        if not created_time:
            return None

        try:
            # If it's already a number (Unix timestamp), handle it
            if isinstance(created_time, (int, float)):
                # If it's less than 1e10, it's in seconds, convert to milliseconds
                if created_time < 1e10:
                    return int(created_time * 1000)
                # Otherwise it's already in milliseconds
                return int(created_time)

            # If it's a string that looks like a number, try to parse it
            if isinstance(created_time, str) and created_time.strip().isdigit():
                timestamp = int(created_time)
                # If it's less than 1e10, it's in seconds, convert to milliseconds
                if timestamp < 1e10:
                    return timestamp * 1000
                # Otherwise it's already in milliseconds
                return timestamp

            # Parse ISO format string
            # Facebook returns ISO format like "2024-01-15T10:30:00+0000" or "2024-01-15T10:30:00Z"
            iso_str = created_time.strip()

            # Normalize timezone formats
            # Replace "+0000" with "+00:00" for fromisoformat compatibility
            if iso_str.endswith("+0000"):
                iso_str = iso_str.replace("+0000", "+00:00")
            # Replace "Z" with "+00:00" for UTC
            elif iso_str.endswith("Z"):
                iso_str = iso_str.replace("Z", "+00:00")
            # If no timezone, assume UTC
            elif "+" not in iso_str and "Z" not in iso_str:
                iso_str = iso_str + "+00:00"

            # Parse ISO string to datetime
            dt = datetime.fromisoformat(iso_str)

            # Convert to UTC timestamp (seconds) then to milliseconds
            # timestamp() returns seconds since epoch in UTC
            timestamp_seconds = dt.timestamp()
            return int(timestamp_seconds * 1000)

        except (ValueError, AttributeError, TypeError) as e:
            logger.warning(
                f"⚠️ Failed to parse created_time: {created_time} | Error: {e}"
            )
            return None
