"""
Socket emitter for comment webhook events.
Handles emitting enriched comment events to page admins via WebSocket.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.database.postgres.repositories.facebook_queries.comments.comment_threads import (
    get_comments_by_ids,
)
from src.utils.logger import get_logger

from .socket_event_builder import build_comment_socket_event

if TYPE_CHECKING:
    from src.socket_service import SocketService

logger = get_logger()


async def emit_comment_event_to_page_admins(
    conn,
    *,
    socket_service: "SocketService",
    page_id: str,
    action: str,
    page_admins: List[Dict[str, Any]],
    conversation: Dict[str, Any],
    mutated_comment: Dict[str, Any],
) -> None:
    """
    Emit enriched comment event to all page admins via SocketService.

    Fetches root and latest comments for enrichment, builds validated socket
    event data, and emits to each admin's WebSocket connection.

    Args:
        conn: Database connection for fetching additional data
        socket_service: SocketService instance for emitting events
        page_id: Facebook page ID
        action: Action type (add, edited, remove, hide, unhide)
        page_admins: List of page admins to emit to
        conversation: Conversation data from get_conversation_with_unread_count
        mutated_comment: The comment that was mutated
    """
    try:
        if not socket_service:
            return

        if not page_admins:
            return

        if not conversation or not mutated_comment:
            logger.warning(
                "⚠️ Missing conversation or mutated_comment for socket event"
            )
            return

        # Fetch root_comment and latest_comment for enrichment
        root_comment, latest_comment = await _fetch_comments_for_enrichment(
            conn, conversation
        )

        if not root_comment:
            logger.warning(
                f"⚠️ Root comment not found: {conversation['root_comment_id']}"
            )
            return

        # Build validated socket event data using Pydantic
        event_data = build_comment_socket_event(
            page_id=page_id,
            action=action,
            conversation=conversation,
            mutated_comment=mutated_comment,
            root_comment=root_comment,
            latest_comment=latest_comment,
        )

        # Convert to dict for socket emission
        event_dict = event_data.model_dump()

        # Emit to each page admin
        await _emit_to_admins(socket_service, page_admins, event_dict)

    except Exception as e:
        logger.error(f"❌ Failed to emit comment event to page admins: {e}")
        # Don't re-raise to avoid breaking comment processing


async def _fetch_comments_for_enrichment(
    conn,
    conversation: Dict[str, Any],
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Fetch root and latest comments needed for socket event enrichment.

    Returns:
        Tuple of (root_comment, latest_comment)
    """
    comment_ids_to_fetch = [conversation["root_comment_id"]]
    if conversation.get("latest_comment_id"):
        comment_ids_to_fetch.append(conversation["latest_comment_id"])

    comments_map = await get_comments_by_ids(conn, list(set(comment_ids_to_fetch)))

    root_comment = comments_map.get(conversation["root_comment_id"])
    latest_comment = None
    if conversation.get("latest_comment_id"):
        latest_comment = comments_map.get(conversation["latest_comment_id"])

    return root_comment, latest_comment


async def _emit_to_admins(
    socket_service: "SocketService",
    page_admins: List[Dict[str, Any]],
    event_dict: Dict[str, Any],
) -> None:
    """
    Emit event to all page admins via WebSocket.
    """
    for admin in page_admins:
        user_id = admin.get("user_id")
        if user_id:
            await socket_service.send_webhook_event(
                user_id=user_id,
                event_type="comment_event",
                event_data=event_dict,
            )
        else:
            logger.warning(f"⚠️ Admin {admin.get('id')} has no user_id")
