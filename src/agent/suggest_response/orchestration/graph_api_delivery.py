"""
Graph API delivery for suggest response agent.
Sends suggestion via Facebook Graph API with retry (messages or comments).
"""

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.common.clients.facebook_graph_page_client import (
    FacebookAPIError,
    FacebookGraphPageClient,
)
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.facebook_queries.comments.comment_conversations import (
    get_conversation_by_id,
)
from src.database.postgres.repositories.facebook_queries.messages.conversations import (
    get_conversation_with_details,
)
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.socket_service import SocketService

logger = get_logger()


def strip_markdown(text: str) -> str:
    """Strip markdown formatting from text for plain-text delivery channels.

    Facebook Messenger / Graph API does not render markdown, so **bold**,
    *italic*, etc. would appear as raw syntax.  This function converts
    common markdown patterns to their plain-text equivalents.
    """
    if not text:
        return text

    # Bold / italic combos: ***text*** or ___text___
    text = re.sub(r"\*{3}(.+?)\*{3}", r"\1", text)
    text = re.sub(r"_{3}(.+?)_{3}", r"\1", text)

    # Bold: **text** or __text__
    text = re.sub(r"\*{2}(.+?)\*{2}", r"\1", text)
    text = re.sub(r"_{2}(.+?)_{2}", r"\1", text)

    # Italic: *text* or _text_ (word-boundary guard for underscores)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)

    # Strikethrough: ~~text~~
    text = re.sub(r"~~(.+?)~~", r"\1", text)

    # Inline code: `text`
    text = re.sub(r"`(.+?)`", r"\1", text)

    # Markdown headings at line start: # Header → Header
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Markdown links: [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Markdown images: ![alt](url) → (removed)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)

    return text


async def deliver_via_graph_api(
    conversation_type: str,
    conversation_id: str,
    page_admin: Dict[str, Any],
    suggestion: Dict[str, Any],
    max_retries: int = 3,
    history_id: Optional[str] = None,
    comment_conversation_service: Optional[Any] = None,
    socket_service: Optional["SocketService"] = None,
    page_service: Optional[Any] = None,
) -> bool:
    """
    Send suggestion via Facebook Graph API with retry.

    Args:
        conversation_type: 'messages' or 'comments'
        conversation_id: facebook_conversation_messages.id or facebook_conversation_comments.id
        page_admin: Page admin dict with access_token, page_id
        suggestion: Suggestion dict - for messages: message, image_urls, video_url;
                    for comments: message, attachment_url
        max_retries: Max retry attempts on failure
        history_id: Optional suggest_response_history id for messages (tagged in metadata for echo)

    Returns:
        True if delivery succeeded, False otherwise (logs error, no admin notification)
    """
    access_token = page_admin.get("access_token")
    if not access_token:
        logger.error(
            f"Graph API delivery failed: No access token for page_admin {page_admin.get('id')}"
        )
        return False

    client = FacebookGraphPageClient(page_access_token=access_token)

    for attempt in range(1, max_retries + 1):
        try:
            if conversation_type == "messages":
                return await _deliver_message(
                    conversation_id, suggestion, client, history_id=history_id
                )
            else:  # comments
                return await _deliver_comment(
                    conversation_id,
                    suggestion,
                    client,
                    history_id=history_id,
                    comment_conversation_service=comment_conversation_service,
                    socket_service=socket_service,
                    page_service=page_service,
                )
        except FacebookAPIError as e:
            logger.error(
                f"Graph API delivery failed (attempt {attempt}/{max_retries}): "
                f"conversation_type={conversation_type}, conversation_id={conversation_id}, "
                f"error={e.message}"
            )
            if attempt < max_retries:
                await asyncio.sleep(1)  # Brief pause before retry
            else:
                return False
        except Exception as e:
            logger.error(
                f"Graph API delivery failed (attempt {attempt}/{max_retries}): "
                f"conversation_type={conversation_type}, conversation_id={conversation_id}, "
                f"error={str(e)}"
            )
            if attempt < max_retries:
                await asyncio.sleep(1)
            else:
                return False

    return False


async def _deliver_message(
    conversation_id: str,
    suggestion: Dict[str, Any],
    client: FacebookGraphPageClient,
    history_id: Optional[str] = None,
) -> bool:
    """Send suggestion as message via Graph API."""
    async with async_db_transaction() as conn:
        conv_details = await get_conversation_with_details(conn, conversation_id)
        if not conv_details:
            logger.error(
                f"Graph API delivery: Conversation not found for messages {conversation_id}"
            )
            return False

        psid = conv_details.get("facebook_page_scope_user_id")
        if not psid:
            logger.error(
                f"Graph API delivery: No PSID for conversation {conversation_id}"
            )
            return False

    message_text = strip_markdown(suggestion.get("message") or "")
    image_urls = suggestion.get("image_urls") or []
    video_url = suggestion.get("video_url")
    reply_to_message_id = suggestion.get("reply_to_message_id")

    if not message_text and not image_urls and not video_url:
        logger.error("Graph API delivery: No content to send for message suggestion")
        return False

    # Tag message so echo webhook can persist metadata (sent_by: ai_agent, history_id)
    metadata_str = None
    if history_id:
        metadata_str = json.dumps({"sent_by": "ai_agent", "history_id": history_id})

    async def _do_send() -> None:
        if message_text:
            await client.send_message(
                user_id=psid,
                message=message_text,
                metadata=metadata_str,
                reply_to_message_id=reply_to_message_id,
            )
        for url in image_urls:
            if url:
                await client.send_image_message(
                    user_id=psid,
                    image_url=url,
                    metadata=metadata_str,
                    reply_to_message_id=reply_to_message_id,
                )
        if video_url:
            await client.send_video_message(
                user_id=psid,
                video_url=video_url,
                metadata=metadata_str,
                reply_to_message_id=reply_to_message_id,
            )

    try:
        await _do_send()
    except FacebookAPIError as e:
        if e.error_code == 10 and e.error_subcode == 2018300:
            logger.info(
                f"Graph API delivery: Taking thread control for conversation {conversation_id} then retrying"
            )
            await client.take_thread_control(recipient_id=psid)
            await _do_send()
        else:
            raise
    return True


async def _deliver_comment(
    conversation_id: str,
    suggestion: Dict[str, Any],
    client: FacebookGraphPageClient,
    history_id: Optional[str] = None,
    comment_conversation_service: Optional[Any] = None,
    socket_service: Optional["SocketService"] = None,
    page_service: Optional[Any] = None,
) -> bool:
    """Reply to comment via Graph API; optionally run immediate emit for UX."""
    async with async_db_transaction() as conn:
        conv = await get_conversation_by_id(conn, conversation_id)
        if not conv:
            logger.error(
                f"Graph API delivery: Conversation not found for comments {conversation_id}"
            )
            return False

        comment_id = conv.get("latest_comment_id")
        if not comment_id:
            comment_id = conv.get("root_comment_id")
        if not comment_id:
            logger.error(
                f"Graph API delivery: No comment to reply to for conversation {conversation_id}"
            )
            return False

    message_text = strip_markdown(suggestion.get("message") or "")
    attachment_url = suggestion.get("attachment_url")

    response = await client.reply_to_comment(
        comment_id=comment_id,
        message=message_text,
        attachment_url=attachment_url,
    )

    new_comment_id = response.get("id") if response else None
    if (
        new_comment_id
        and comment_conversation_service
        and socket_service
        and page_service
    ):
        try:
            async with async_db_transaction() as conn:
                page_admins = await page_service.get_facebook_page_admins_by_page_id(
                    conn, conv["fan_page_id"]
                )
                from src.services.facebook.comments._internal.immediate_emit import (
                    process_outgoing_comment_reply,
                )

                await process_outgoing_comment_reply(
                    conn,
                    new_comment_id=new_comment_id,
                    parent_comment_id=comment_id,
                    post_id=conv["post_id"],
                    fan_page_id=conv["fan_page_id"],
                    message=message_text,
                    attachment_url=attachment_url,
                    metadata={
                        "sent_by": "ai_agent",
                        **({"history_id": history_id} if history_id else {}),
                    },
                    page_admins=page_admins,
                    socket_service=socket_service,
                    comment_conversation_service=comment_conversation_service,
                )
        except Exception as e:
            logger.error(
                f"❌ Immediate emit for AI comment reply failed: {e}",
                exc_info=True,
            )

    return True
