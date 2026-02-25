"""
Immediate emit for outgoing comment replies (admin or AI).
Pre-inserts comment, syncs conversation, and emits socket event so FE shows the comment
instantly instead of waiting for the Facebook webhook.
"""

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.database.postgres.repositories.facebook_queries.comments.comment_records import (
    create_comment,
    get_comment,
)
from src.database.postgres.repositories.facebook_queries.comments.comment_conversations import (
    get_conversation_id_for_comment,
    get_conversation_by_id,
)
from src.database.postgres.utils import get_current_timestamp
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.socket_service import SocketService

logger = get_logger()


async def process_outgoing_comment_reply(
    conn,
    *,
    new_comment_id: str,
    parent_comment_id: str,
    post_id: str,
    fan_page_id: str,
    message: Optional[str] = None,
    attachment_url: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    page_admins: List[Dict[str, Any]],
    socket_service: "SocketService",
    comment_conversation_service: Any,
) -> None:
    """
    After a successful Graph API reply, pre-insert the comment, sync conversation,
    and emit socket event so the frontend shows the reply immediately.

    Call this from both admin reply (api_handler) and AI agent reply (graph_api_delivery).

    Args:
        conn: Database connection (caller must run inside a transaction).
        new_comment_id: Comment ID returned by Facebook Graph API.
        parent_comment_id: The comment we replied to (for tree and conversation).
        post_id: Post ID.
        fan_page_id: Page ID.
        message: Comment text.
        attachment_url: Optional image URL (stored as photo_url).
        metadata: E.g. {"sent_by": "admin"} or {"sent_by": "ai_agent", "history_id": "..."}.
        page_admins: List of page admin dicts (for socket emission).
        socket_service: SocketService instance.
        comment_conversation_service: CommentConversationService instance.
    """
    try:
        current_time = get_current_timestamp()

        await create_comment(
            conn=conn,
            comment_id=new_comment_id,
            post_id=post_id,
            fan_page_id=fan_page_id,
            parent_comment_id=parent_comment_id,
            is_from_page=True,
            facebook_page_scope_user_id=None,
            message=message,
            photo_url=attachment_url,
            video_url=None,
            facebook_created_time=current_time,
            metadata=metadata,
            created_at=current_time,
            updated_at=current_time,
        )

        conv_id = await get_conversation_id_for_comment(conn, parent_comment_id)
        if conv_id:
            conv = await get_conversation_by_id(conn, conv_id)
            root_comment_id = conv["root_comment_id"] if conv else parent_comment_id
        else:
            root_comment_id = parent_comment_id

        created_row = await get_comment(conn, new_comment_id)
        if not created_row:
            logger.warning(
                f"⚠️ Immediate emit: get_comment failed for {new_comment_id}"
            )
            return

        conversation_summary = await comment_conversation_service.sync_single_comment_to_conversation(
            conn=conn,
            fan_page_id=fan_page_id,
            post_id=post_id,
            root_comment_id=root_comment_id,
            comment=created_row,
            verb="add",
        )

        if not conversation_summary:
            logger.warning(
                f"⚠️ Immediate emit: sync_single_comment_to_conversation returned None for {new_comment_id}"
            )
            return

        from .socket_emitter import emit_comment_event_to_page_admins

        await emit_comment_event_to_page_admins(
            conn=conn,
            socket_service=socket_service,
            page_id=fan_page_id,
            action="add",
            page_admins=page_admins,
            conversation=conversation_summary,
            mutated_comment=created_row,
        )
    except Exception as e:
        logger.error(
            f"❌ Immediate emit failed for comment {new_comment_id}: {e}",
            exc_info=True,
        )
        raise
