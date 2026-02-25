import json
from typing import Optional, Dict, Any, List, Tuple

import asyncpg

from src.database.postgres.executor import (
    execute_async_single,
    execute_async_returning,
    execute_async_query,
)
from src.database.postgres.utils import get_current_timestamp


async def get_conversation_by_root_comment_id(
    conn: asyncpg.Connection, root_comment_id: str
) -> Optional[Dict[str, Any]]:
    query = """
        SELECT 
            fcc.id::text AS id, 
            fcc.root_comment_id, 
            fcc.fan_page_id, 
            fcc.post_id, 
            fcc.participant_scope_users,
            fcc.has_page_reply, 
            fcc.latest_comment_is_from_page, 
            fcc.latest_comment_id,
            fcc.latest_comment_facebook_time, 
            fcc.page_last_seen_comment_id, 
            fcc.page_last_seen_at,
            fcc.mark_as_read, 
            fcc.created_at, 
            fcc.updated_at,
            COALESCE(counts.total_comments, 0) AS total_comments,
            COALESCE(counts.unread_count, 0) AS unread_count
        FROM facebook_conversation_comments fcc
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) AS total_comments,
                COUNT(*) FILTER (
                    WHERE c.is_from_page = FALSE 
                    AND c.deleted_at IS NULL
                    AND c.page_seen_at IS NULL
                ) AS unread_count
            FROM facebook_conversation_comment_entries e
            JOIN comments c ON c.id = e.comment_id
            WHERE e.conversation_id = fcc.id
              AND c.deleted_at IS NULL
        ) counts ON true
        WHERE fcc.root_comment_id = $1
    """
    return await execute_async_single(conn, query, root_comment_id)


async def get_conversation_by_id(
    conn: asyncpg.Connection, conversation_id: str
) -> Optional[Dict[str, Any]]:
    query = """
        SELECT
            fcc.id::text AS id,
            fcc.root_comment_id,
            fcc.fan_page_id,
            fcc.post_id,
            fcc.participant_scope_users,
            fcc.has_page_reply,
            fcc.latest_comment_is_from_page,
            fcc.latest_comment_id,
            fcc.latest_comment_facebook_time,
            fcc.page_last_seen_comment_id,
            fcc.page_last_seen_at,
            fcc.mark_as_read,
            fcc.created_at,
            fcc.updated_at,
            fp.name AS page_name,
            fp.avatar AS page_avatar,
            fp.category AS page_category,
            fp.created_at AS page_created_at,
            fp.updated_at AS page_updated_at,
            p.message AS post_message,
            p.video_link AS post_video_link,
            p.photo_link AS post_photo_link,
            p.facebook_created_time AS post_facebook_created_time,
            p.created_at AS post_created_at,
            p.updated_at AS post_updated_at
        FROM facebook_conversation_comments fcc
        JOIN fan_pages fp ON fp.id = fcc.fan_page_id
        JOIN posts p ON p.id = fcc.post_id
        WHERE fcc.id = $1
    """
    return await execute_async_single(conn, query, conversation_id)


async def get_comments_thread_contexts_batch(
    conn: asyncpg.Connection, fcc_ids: List[str]
) -> Dict[str, Dict[str, Any]]:
    """Get thread context (post, participants, page) for multiple comment conversations.
    Returns dict mapping fcc_id (str) -> {post, participants, page}.
    """
    if not fcc_ids:
        return {}

    query = """
        SELECT
            fcc.id::text AS fcc_id,
            fp.id AS page_id,
            fp.name AS page_name,
            fp.avatar AS page_avatar,
            fp.category AS page_category,
            p.id AS post_id,
            p.message AS post_message,
            p.full_picture AS post_full_picture,
            p.photo_link AS post_photo_link,
            fcc.participant_scope_users
        FROM facebook_conversation_comments fcc
        JOIN fan_pages fp ON fp.id = fcc.fan_page_id
        JOIN posts p ON p.id = fcc.post_id
        WHERE fcc.id = ANY($1::uuid[])
    """
    rows = await execute_async_query(conn, query, fcc_ids)
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        fcc_id = row.get("fcc_id")
        if not fcc_id:
            continue
        participants_raw = row.get("participant_scope_users") or []
        if isinstance(participants_raw, str):
            try:
                participants_raw = json.loads(participants_raw)
            except (json.JSONDecodeError, TypeError):
                participants_raw = []
        if not isinstance(participants_raw, list):
            participants_raw = []

        participants = []
        for p in participants_raw:
            if isinstance(p, dict):
                participants.append({
                    "facebook_page_scope_user_id": p.get("facebook_page_scope_user_id"),
                    "name": p.get("name"),
                    "avatar": p.get("avatar") or p.get("profile_pic"),
                })
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                participants.append({
                    "facebook_page_scope_user_id": p[0] if p else None,
                    "name": p[1] if len(p) > 1 else None,
                    "avatar": p[2] if len(p) > 2 else None,
                })

        out[fcc_id] = {
            "post": {
                "id": str(row.get("post_id") or ""),
                "message": row.get("post_message"),
                "full_picture": row.get("post_full_picture"),
                "photo_link": row.get("post_photo_link"),
            },
            "participants": participants,
            "page": {
                "id": str(row.get("page_id") or ""),
                "name": row.get("page_name"),
                "avatar": row.get("page_avatar"),
                "category": row.get("page_category"),
            },
        }
    return out


async def create_conversation(
    conn: asyncpg.Connection,
    root_comment_id: str,
    fan_page_id: str,
    post_id: str,
    latest_comment_id: Optional[str] = None,
    latest_comment_facebook_time: Optional[int] = None,
    latest_comment_is_from_page: Optional[bool] = None,
    has_page_reply: bool = False,
    participant_scope_users: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    current_time = get_current_timestamp()
    participants_json = json.dumps(participant_scope_users or [])

    query = """
        INSERT INTO facebook_conversation_comments (
            root_comment_id,
            fan_page_id,
            post_id,
            participant_scope_users,
            latest_comment_id,
            latest_comment_facebook_time,
            latest_comment_is_from_page,
            has_page_reply,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (root_comment_id) DO UPDATE SET
            participant_scope_users = EXCLUDED.participant_scope_users,
            latest_comment_id = EXCLUDED.latest_comment_id,
            latest_comment_facebook_time = EXCLUDED.latest_comment_facebook_time,
            latest_comment_is_from_page = EXCLUDED.latest_comment_is_from_page,
            has_page_reply = EXCLUDED.has_page_reply,
            updated_at = EXCLUDED.updated_at
        RETURNING id::text, root_comment_id, fan_page_id, post_id, participant_scope_users,
            has_page_reply, latest_comment_is_from_page, latest_comment_id,
            latest_comment_facebook_time, page_last_seen_comment_id, page_last_seen_at,
            mark_as_read, created_at, updated_at
    """

    return await execute_async_returning(
        conn,
        query,
        root_comment_id,
        fan_page_id,
        post_id,
        participants_json,
        latest_comment_id,
        latest_comment_facebook_time,
        latest_comment_is_from_page,
        has_page_reply,
        current_time,
        current_time,
    )


async def update_conversation(
    conn: asyncpg.Connection,
    conversation_id: str,
    *,
    latest_comment_id: Optional[str] = None,
    latest_comment_facebook_time: Optional[int] = None,
    latest_comment_is_from_page: Optional[bool] = None,
    has_page_reply: Optional[bool] = None,
    participant_scope_users: Optional[List[Dict[str, Any]]] = None,
    page_last_seen_comment_id: Optional[str] = None,
    page_last_seen_at: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    updates = []
    params: List[Any] = []
    idx = 1

    if latest_comment_id is not None:
        updates.append(f"latest_comment_id = ${idx}")
        params.append(latest_comment_id)
        idx += 1

    if latest_comment_facebook_time is not None:
        updates.append(f"latest_comment_facebook_time = ${idx}")
        params.append(latest_comment_facebook_time)
        idx += 1

    if latest_comment_is_from_page is not None:
        updates.append(f"latest_comment_is_from_page = ${idx}")
        params.append(latest_comment_is_from_page)
        idx += 1

    if has_page_reply is not None:
        updates.append(f"has_page_reply = ${idx}")
        params.append(has_page_reply)
        idx += 1

    if participant_scope_users is not None:
        updates.append(f"participant_scope_users = ${idx}::jsonb")
        params.append(json.dumps(participant_scope_users))
        idx += 1

    if page_last_seen_comment_id is not None:
        updates.append(f"page_last_seen_comment_id = ${idx}")
        params.append(page_last_seen_comment_id)
        idx += 1

    if page_last_seen_at is not None:
        updates.append(f"page_last_seen_at = ${idx}")
        params.append(page_last_seen_at)
        idx += 1

    if not updates:
        return await get_conversation_by_id(conn, conversation_id)

    current_time = get_current_timestamp()
    updates.append(f"updated_at = ${idx}")
    params.append(current_time)
    idx += 1

    query = f"""
        UPDATE facebook_conversation_comments
        SET {', '.join(updates)}
        WHERE id = ${idx}
        RETURNING id::text, root_comment_id, fan_page_id, post_id, participant_scope_users,
            has_page_reply, latest_comment_is_from_page, latest_comment_id,
            latest_comment_facebook_time, page_last_seen_comment_id, page_last_seen_at,
            mark_as_read, created_at, updated_at
    """
    params.append(conversation_id)

    return await execute_async_returning(conn, query, *params)


async def refresh_conversation_latest_comment(
    conn: asyncpg.Connection,
    conversation_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Refresh latest comment metadata on the conversation by querying the actual latest comment from database.

    This is useful after bulk sync operations to ensure latest_comment_* fields are accurate.
    Finds the comment with the highest facebook_created_time (or created_at if facebook_created_time is NULL).
    """
    # Query the latest comment by facebook_created_time (prefer facebook_created_time, fallback to created_at)
    query = """
        SELECT 
            c.id,
            c.is_from_page,
            COALESCE(c.facebook_created_time, c.created_at) as effective_timestamp
        FROM facebook_conversation_comment_entries e
        JOIN comments c ON c.id = e.comment_id
        WHERE e.conversation_id = $1
          AND c.deleted_at IS NULL
        ORDER BY 
            COALESCE(c.facebook_created_time, c.created_at) DESC,
            c.id DESC
        LIMIT 1
    """

    latest_comment = await execute_async_single(conn, query, conversation_id)

    if not latest_comment:
        # No comments found, return None
        return None

    # Update conversation with the latest comment
    return await update_conversation(
        conn=conn,
        conversation_id=conversation_id,
        latest_comment_id=latest_comment["id"],
        latest_comment_is_from_page=latest_comment["is_from_page"],
        latest_comment_facebook_time=latest_comment.get("effective_timestamp"),
    )


async def upsert_conversation_entry(
    conn: asyncpg.Connection,
    conversation_id: str,
    comment_id: str,
    *,
    is_root_comment: bool = False,
) -> Dict[str, Any]:
    current_time = get_current_timestamp()
    query = """
        INSERT INTO facebook_conversation_comment_entries (
            conversation_id,
            comment_id,
            is_root_comment,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (conversation_id, comment_id) DO UPDATE SET
            is_root_comment = EXCLUDED.is_root_comment,
            updated_at = EXCLUDED.updated_at
        RETURNING id::text, conversation_id::text, comment_id, is_root_comment, created_at, updated_at
    """
    return await execute_async_returning(
        conn,
        query,
        conversation_id,
        comment_id,
        is_root_comment,
        current_time,
        current_time,
    )


async def delete_conversation_entry(
    conn: asyncpg.Connection, comment_id: str
) -> Optional[Dict[str, Any]]:
    query = """
        DELETE FROM facebook_conversation_comment_entries
        WHERE comment_id = $1
        RETURNING conversation_id::text, is_root_comment
    """
    return await execute_async_single(conn, query, comment_id)


async def get_latest_comment_in_conversation(
    conn: asyncpg.Connection, conversation_id: str
) -> Optional[Dict[str, Any]]:
    query = """
        SELECT c.*
        FROM facebook_conversation_comment_entries e
        JOIN comments c ON c.id = e.comment_id
        WHERE e.conversation_id = $1
            AND c.deleted_at IS NULL
        ORDER BY COALESCE(c.facebook_created_time, 0) DESC,
                 c.created_at DESC
        LIMIT 1
    """
    return await execute_async_single(conn, query, conversation_id)


async def get_conversation_id_for_comment(
    conn: asyncpg.Connection, comment_id: str
) -> Optional[str]:
    query = """
        SELECT conversation_id::text
        FROM facebook_conversation_comment_entries
        WHERE comment_id = $1
        LIMIT 1
    """
    row = await execute_async_single(conn, query, comment_id)
    return row["conversation_id"] if row else None


async def list_conversations_for_pages(
    conn: asyncpg.Connection,
    page_ids: List[str],
    limit: int,
    cursor: Optional[Tuple[int, str]] = None,
):
    if not page_ids:
        return [], False, None

    base_limit = max(1, min(limit, 50))
    query = """
        SELECT
            fcc.id::text AS id,
            fcc.root_comment_id,
            fcc.fan_page_id,
            fcc.post_id,
            fcc.participant_scope_users,
            fcc.has_page_reply,
            fcc.latest_comment_is_from_page,
            fcc.latest_comment_id,
            fcc.latest_comment_facebook_time,
            fcc.page_last_seen_comment_id,
            fcc.page_last_seen_at,
            fcc.mark_as_read,
            fcc.created_at,
            fcc.updated_at,
            COALESCE(fcc.latest_comment_facebook_time, fcc.updated_at) AS sort_value,
            counts.total_comments,
            counts.unread_count,
            fp.name AS page_name,
            fp.avatar AS page_avatar,
            fp.category AS page_category,
            fp.created_at AS page_created_at,
            fp.updated_at AS page_updated_at,
            p.message AS post_message,
            p.video_link AS post_video_link,
            p.photo_link AS post_photo_link,
            p.facebook_created_time AS post_facebook_created_time,
            p.reaction_total_count AS post_reaction_total_count,
            p.reaction_like_count AS post_reaction_like_count,
            p.reaction_love_count AS post_reaction_love_count,
            p.reaction_haha_count AS post_reaction_haha_count,
            p.reaction_wow_count AS post_reaction_wow_count,
            p.reaction_sad_count AS post_reaction_sad_count,
            p.reaction_angry_count AS post_reaction_angry_count,
            p.reaction_care_count AS post_reaction_care_count,
            p.share_count AS post_share_count,
            p.comment_count AS post_comment_count,
            p.full_picture AS post_full_picture,
            p.permalink_url AS post_permalink_url,
            p.status_type AS post_status_type,
            p.is_published AS post_is_published,
            p.reactions_fetched_at AS post_reactions_fetched_at,
            p.engagement_fetched_at AS post_engagement_fetched_at,
            p.created_at AS post_created_at,
            p.updated_at AS post_updated_at
        FROM facebook_conversation_comments fcc
        JOIN fan_pages fp ON fp.id = fcc.fan_page_id
        JOIN posts p ON p.id = fcc.post_id
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) AS total_comments,
                COUNT(*) FILTER (
                    WHERE c.is_from_page = FALSE 
                    AND c.deleted_at IS NULL
                    AND c.page_seen_at IS NULL
                ) AS unread_count
            FROM facebook_conversation_comment_entries e
            JOIN comments c ON c.id = e.comment_id
            WHERE e.conversation_id = fcc.id
              AND c.deleted_at IS NULL
        ) counts ON true
        WHERE fcc.fan_page_id = ANY($1)
    """

    params: List[Any] = [page_ids, base_limit + 1]

    if cursor:
        query += """
        AND (
            COALESCE(fcc.latest_comment_facebook_time, fcc.updated_at) < $3
            OR (
                COALESCE(fcc.latest_comment_facebook_time, fcc.updated_at) = $3
                AND fcc.id::text < $4
            )
        )
        """
        params.extend([cursor[0], cursor[1]])

    query += """
        ORDER BY sort_value DESC, fcc.id DESC
        LIMIT $2
    """

    rows = await execute_async_query(conn, query, *params)

    has_more = len(rows) > base_limit
    if has_more:
        rows = rows[:base_limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = (
            last.get("sort_value") or 0,
            str(last.get("id")),
        )

    return rows, has_more, next_cursor


async def list_thread_comments(
    conn: asyncpg.Connection,
    conversation_id: str,
    limit: int,
    cursor: Optional[Tuple[int, str]] = None,
):
    base_limit = max(1, min(limit, 100))
    query = """
        SELECT
            c.*,
            CASE WHEN c.is_from_page THEN 'page' ELSE 'user' END AS author_kind,
            fpsu.id AS fpsu_id,
            fpsu.user_info->>'name' AS fpsu_name,
            fpsu.user_info->>'profile_pic' AS fpsu_profile_pic,
            fp.name as page_name,
            fp.avatar as page_avatar,
            fp.category as page_category,
            p.message as post_message,
            fcc.root_comment_id,
            c.created_at AS sort_value
        FROM facebook_conversation_comment_entries e
        JOIN facebook_conversation_comments fcc ON fcc.id = e.conversation_id
        JOIN comments c ON c.id = e.comment_id
        LEFT JOIN facebook_page_scope_users fpsu ON c.facebook_page_scope_user_id = fpsu.id
        JOIN fan_pages fp ON c.fan_page_id = fp.id
        JOIN posts p ON c.post_id = p.id
        WHERE e.conversation_id = $1
    """

    params: List[Any] = [conversation_id, base_limit + 1]

    if cursor:
        query += """
        AND (c.created_at, c.id) > ($3, $4)
        """
        params.extend([cursor[0], cursor[1]])

    query += """
        ORDER BY c.created_at ASC, c.id ASC
        LIMIT $2
    """

    rows = await execute_async_query(conn, query, *params)

    has_more = len(rows) > base_limit
    if has_more:
        rows = rows[:base_limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = (last.get("sort_value") or last["created_at"], last["id"])

    return rows, has_more, next_cursor


async def mark_all_comments_as_seen(
    conn: asyncpg.Connection, conversation_id: str
) -> Optional[Dict[str, Any]]:
    """
    Mark all user comments in a conversation as seen by:
    1. Setting page_seen_at for all user comments in the conversation
    2. Updating conversation cursor (page_last_seen_comment_id, page_last_seen_at)
    """
    current_time = get_current_timestamp()

    # Mark all user comments as seen
    mark_comments_query = """
        UPDATE comments c
        SET page_seen_at = $2, updated_at = $2
        FROM facebook_conversation_comment_entries e
        WHERE e.comment_id = c.id
          AND e.conversation_id = $1
          AND c.is_from_page = FALSE
          AND c.deleted_at IS NULL
          AND c.page_seen_at IS NULL
    """
    await execute_async_query(conn, mark_comments_query, conversation_id, current_time)

    # Update conversation cursor
    query = """
        UPDATE facebook_conversation_comments
        SET 
            page_last_seen_comment_id = latest_comment_id,
            page_last_seen_at = $2,
            updated_at = $2
        WHERE id = $1
        RETURNING id::text, root_comment_id, fan_page_id, post_id, participant_scope_users,
            has_page_reply, latest_comment_is_from_page, latest_comment_id,
            latest_comment_facebook_time, page_last_seen_comment_id, page_last_seen_at,
            mark_as_read, created_at, updated_at
    """
    return await execute_async_returning(conn, query, conversation_id, current_time)


async def update_conversation_mark_as_read(
    conn: asyncpg.Connection,
    conversation_id: str,
    mark_as_read: bool,
) -> Optional[Dict[str, Any]]:
    """
    Toggle mark_as_read boolean status for a conversation (UX feature).
    This is separate from marking comments as seen (page_seen_at).
    """
    current_time = get_current_timestamp()

    query = """
        UPDATE facebook_conversation_comments
        SET 
            mark_as_read = $2,
            updated_at = $3
        WHERE id = $1
        RETURNING id::text, root_comment_id, fan_page_id, post_id, participant_scope_users,
            has_page_reply, latest_comment_is_from_page, latest_comment_id,
            latest_comment_facebook_time, page_last_seen_comment_id, page_last_seen_at,
            mark_as_read, created_at, updated_at
    """
    return await execute_async_returning(
        conn, query, conversation_id, mark_as_read, current_time
    )


async def count_unread_comments_in_conversation(
    conn: asyncpg.Connection, conversation_id: str
) -> int:
    """
    Count unread comments based on page_seen_at per-comment tracking.
    Unread = user comments where page_seen_at IS NULL.
    """
    query = """
        SELECT COUNT(*) AS unread_count
        FROM facebook_conversation_comment_entries e
        JOIN comments c ON c.id = e.comment_id
        WHERE e.conversation_id = $1
          AND c.is_from_page = FALSE
          AND c.deleted_at IS NULL
          AND c.page_seen_at IS NULL
    """
    result = await execute_async_single(conn, query, conversation_id)
    return result["unread_count"] if result else 0


async def get_conversation_with_unread_count(
    conn: asyncpg.Connection, conversation_id: str
) -> Optional[Dict[str, Any]]:
    """Get conversation with computed unread_count and total_comments."""
    query = """
        SELECT
            fcc.id::text AS id,
            fcc.root_comment_id,
            fcc.fan_page_id,
            fcc.post_id,
            fcc.participant_scope_users,
            fcc.has_page_reply,
            fcc.latest_comment_is_from_page,
            fcc.latest_comment_id,
            fcc.latest_comment_facebook_time,
            fcc.page_last_seen_comment_id,
            fcc.page_last_seen_at,
            fcc.mark_as_read,
            fcc.created_at,
            fcc.updated_at,
            counts.total_comments,
            counts.unread_count,
            fp.name AS page_name,
            fp.avatar AS page_avatar,
            fp.category AS page_category,
            fp.created_at AS page_created_at,
            fp.updated_at AS page_updated_at,
            p.message AS post_message,
            p.video_link AS post_video_link,
            p.photo_link AS post_photo_link,
            p.facebook_created_time AS post_facebook_created_time,
            p.reaction_total_count AS post_reaction_total_count,
            p.reaction_like_count AS post_reaction_like_count,
            p.reaction_love_count AS post_reaction_love_count,
            p.reaction_haha_count AS post_reaction_haha_count,
            p.reaction_wow_count AS post_reaction_wow_count,
            p.reaction_sad_count AS post_reaction_sad_count,
            p.reaction_angry_count AS post_reaction_angry_count,
            p.reaction_care_count AS post_reaction_care_count,
            p.share_count AS post_share_count,
            p.comment_count AS post_comment_count,
            p.full_picture AS post_full_picture,
            p.permalink_url AS post_permalink_url,
            p.status_type AS post_status_type,
            p.is_published AS post_is_published,
            p.reactions_fetched_at AS post_reactions_fetched_at,
            p.engagement_fetched_at AS post_engagement_fetched_at,
            p.created_at AS post_created_at,
            p.updated_at AS post_updated_at
        FROM facebook_conversation_comments fcc
        JOIN fan_pages fp ON fp.id = fcc.fan_page_id
        JOIN posts p ON p.id = fcc.post_id
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) AS total_comments,
                COUNT(*) FILTER (
                    WHERE c.is_from_page = FALSE 
                    AND c.deleted_at IS NULL
                    AND c.page_seen_at IS NULL
                ) AS unread_count
            FROM facebook_conversation_comment_entries e
            JOIN comments c ON c.id = e.comment_id
            WHERE e.conversation_id = fcc.id
              AND c.deleted_at IS NULL
        ) counts ON true
        WHERE fcc.id = $1
    """
    return await execute_async_single(conn, query, conversation_id)
