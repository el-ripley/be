"""
Escalation service: business logic for agent_escalations.
List/update escalations for the current user (owner_user_id).
"""

from typing import Any, Dict, List, Optional

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories import (
    count_escalations_with_filters,
    get_escalation_by_id,
    get_escalation_messages,
    get_escalations_with_filters,
    insert_escalation_message,
    update_escalation_status,
)
from src.database.postgres.repositories.facebook_queries.messages import (
    get_conversations_with_details_batch,
)
from src.database.postgres.repositories.facebook_queries.comments import (
    get_comments_thread_contexts_batch,
)
from src.utils.logger import get_logger

logger = get_logger()


async def _enrich_items_with_thread_context(
    conn: Any, items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Enrich escalation items with thread_context for UI preview."""
    if not items:
        return items

    message_ids: List[str] = []
    comments_ids: List[str] = []

    for it in items:
        ct = it.get("conversation_type")
        if ct == "messages":
            cid = it.get("facebook_conversation_messages_id")
            if cid:
                message_ids.append(cid)
        elif ct == "comments":
            cid = it.get("facebook_conversation_comments_id")
            if cid:
                comments_ids.append(str(cid))

    messages_ctx: Dict[str, Dict[str, Any]] = {}
    if message_ids:
        messages_ctx = await get_conversations_with_details_batch(conn, message_ids)

    comments_ctx: Dict[str, Dict[str, Any]] = {}
    if comments_ids:
        comments_ctx = await get_comments_thread_contexts_batch(conn, comments_ids)

    from src.api.escalations.schemas import (
        CommentsThreadContext,
        ConversationParticipant,
        MessagesThreadContext,
        PageInfo,
        PostInfo,
    )

    out: List[Dict[str, Any]] = []
    for it in items:
        item = dict(it)
        ct = item.get("conversation_type")

        if ct == "messages":
            cid = item.get("facebook_conversation_messages_id")
            ctx_raw = messages_ctx.get(cid) if cid else None
            if ctx_raw:
                page = None
                if ctx_raw.get("fan_page_id"):
                    page = PageInfo(
                        id=str(ctx_raw["fan_page_id"]),
                        name=ctx_raw.get("page_name"),
                        avatar=ctx_raw.get("page_avatar"),
                        category=ctx_raw.get("page_category"),
                    )
                item["thread_context"] = MessagesThreadContext(
                    user_info=ctx_raw.get("user_info"),
                    page=page,
                )
        elif ct == "comments":
            cid = str(item.get("facebook_conversation_comments_id") or "")
            ctx_raw = comments_ctx.get(cid) if cid else None
            if ctx_raw:
                post_data = ctx_raw.get("post") or {}
                post = PostInfo(
                    id=post_data.get("id", ""),
                    message=post_data.get("message"),
                    full_picture=post_data.get("full_picture"),
                    photo_link=post_data.get("photo_link"),
                )
                page_data = ctx_raw.get("page") or {}
                page = PageInfo(
                    id=str(page_data.get("id", "")),
                    name=page_data.get("name"),
                    avatar=page_data.get("avatar"),
                    category=page_data.get("category"),
                )
                participants = [
                    ConversationParticipant(
                        facebook_page_scope_user_id=p.get(
                            "facebook_page_scope_user_id"
                        ),
                        name=p.get("name"),
                        avatar=p.get("avatar"),
                    )
                    for p in ctx_raw.get("participants") or []
                ]
                item["thread_context"] = CommentsThreadContext(
                    post=post,
                    participants=participants,
                    page=page,
                )
        out.append(item)
    return out


class EscalationService:
    """Service for managing agent escalations (user-facing list/update)."""

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
        """
        List escalations for the given user (owner_user_id) with optional filters.
        created_at_from/created_at_to: Unix timestamp in milliseconds (inclusive).

        Returns:
            Dict with 'items' (list of escalation records) and 'total' (count)
        """
        async with async_db_transaction() as conn:
            items = await get_escalations_with_filters(
                conn,
                owner_user_id=user_id,
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
            total = await count_escalations_with_filters(
                conn,
                owner_user_id=user_id,
                conversation_type=conversation_type,
                fan_page_id=fan_page_id,
                facebook_conversation_messages_id=facebook_conversation_messages_id,
                facebook_conversation_comments_id=facebook_conversation_comments_id,
                status=status,
                priority=priority,
                created_at_from=created_at_from,
                created_at_to=created_at_to,
            )
            items = await _enrich_items_with_thread_context(conn, items)
        return {"items": items, "total": total}

    async def get_escalation_detail(
        self, user_id: str, escalation_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get single escalation with its messages. Verifies ownership.

        Returns:
            Escalation dict with 'messages' key, or None if not found / not owner
        """
        async with async_db_transaction() as conn:
            escalation = await get_escalation_by_id(conn, escalation_id)
            if not escalation:
                return None
            if str(escalation.get("owner_user_id")) != str(user_id):
                raise PermissionError(
                    f"Escalation {escalation_id} does not belong to user {user_id}"
                )
            messages = await get_escalation_messages(conn, escalation_id)
            escalation["messages"] = messages
        return escalation

    async def update_escalation(
        self,
        user_id: str,
        escalation_id: str,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Update escalation status (open/closed). Verifies escalation belongs to user.

        Returns:
            Updated escalation record or None if not found / not owner
        """
        async with async_db_transaction() as conn:
            existing = await get_escalation_by_id(conn, escalation_id)
            if not existing:
                return None
            if str(existing.get("owner_user_id")) != str(user_id):
                raise PermissionError(
                    f"Escalation {escalation_id} does not belong to user {user_id}"
                )
            if status is None:
                return existing
            updated = await update_escalation_status(conn, escalation_id, status)
        return updated

    async def add_message(
        self,
        user_id: str,
        escalation_id: str,
        content: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Add a message to an escalation thread (sender_type = 'user').
        Verifies escalation belongs to user.

        Returns:
            The created message record, or None if escalation not found / not owner
        """
        async with async_db_transaction() as conn:
            escalation = await get_escalation_by_id(conn, escalation_id)
            if not escalation:
                return None
            if str(escalation.get("owner_user_id")) != str(user_id):
                raise PermissionError(
                    f"Escalation {escalation_id} does not belong to user {user_id}"
                )
            message = await insert_escalation_message(
                conn,
                escalation_id=escalation_id,
                sender_type="user",
                content=content,
            )
        return message
