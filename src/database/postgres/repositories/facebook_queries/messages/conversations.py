"""
Facebook Conversation Database Operations.

Handles CRUD and state management for Facebook Messenger conversations.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from src.database.postgres.executor import (
    execute_async_query,
    execute_async_returning,
    execute_async_single,
)
from src.database.postgres.utils import get_current_timestamp

# ================================================================
# CONVERSATION RETRIEVAL
# ================================================================


async def get_conversation_by_participants(
    conn: asyncpg.Connection,
    fan_page_id: str,
    facebook_page_scope_user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Get conversation by page and user IDs with full hydrated data.

    Returns None if conversation doesn't exist.
    """
    query = """
        SELECT 
            c.id as conversation_id,
            c.fan_page_id,
            c.facebook_page_scope_user_id,
            c.latest_message_id,
            c.latest_message_is_from_page,
            c.latest_message_facebook_time,
            c.page_last_seen_message_id,
            c.page_last_seen_at,
            c.user_seen_at,
            c.participants_snapshot,
            c.ad_context,
            c.created_at as conversation_created_at,
            c.updated_at as conversation_updated_at,
            fp.name as page_name,
            fp.avatar as page_avatar,
            fp.category as page_category,
            fpsu.user_info,
            counts.total_messages,
            counts.unread_count,
            c.mark_as_read
        FROM facebook_conversation_messages c
        JOIN fan_pages fp ON c.fan_page_id = fp.id
        JOIN facebook_page_scope_users fpsu ON c.facebook_page_scope_user_id = fpsu.id
        LEFT JOIN LATERAL (
            SELECT 
                COUNT(*) as total_messages,
                COUNT(*) FILTER (WHERE NOT is_echo AND page_seen_at IS NULL) as unread_count
            FROM messages m
            WHERE m.conversation_id = c.id
        ) counts ON true
        WHERE c.fan_page_id = $1 AND c.facebook_page_scope_user_id = $2
    """

    result = await execute_async_returning(
        conn, query, fan_page_id, facebook_page_scope_user_id
    )

    if not result:
        return None

    return _parse_conversation_json_fields(result)


async def get_conversation_with_details(
    conn: asyncpg.Connection,
    conversation_id: str,
) -> Dict[str, Any]:
    """Get conversation with joined fanpage and user data by conversation ID."""
    query = """
        SELECT 
            c.id as conversation_id,
            c.fan_page_id,
            c.facebook_page_scope_user_id,
            c.latest_message_id,
            c.latest_message_is_from_page,
            c.latest_message_facebook_time,
            c.page_last_seen_message_id,
            c.page_last_seen_at,
            c.user_seen_at,
            c.ad_context,
            c.created_at as conversation_created_at,
            c.updated_at as conversation_updated_at,
            fp.name as page_name,
            fp.avatar as page_avatar,
            fp.category as page_category,
            fpsu.user_info,
            counts.total_messages,
            counts.unread_count,
            c.mark_as_read
        FROM facebook_conversation_messages c
        JOIN fan_pages fp ON c.fan_page_id = fp.id
        JOIN facebook_page_scope_users fpsu ON c.facebook_page_scope_user_id = fpsu.id
        LEFT JOIN LATERAL (
            SELECT 
                COUNT(*) as total_messages,
                COUNT(*) FILTER (WHERE NOT is_echo AND page_seen_at IS NULL) as unread_count
            FROM messages m
            WHERE m.conversation_id = c.id
        ) counts ON true
        WHERE c.id = $1 AND c.deleted_at IS NULL
    """

    result = await execute_async_returning(conn, query, conversation_id)

    if not result:
        return result

    return _parse_conversation_json_fields(result)


async def get_conversations_with_details_batch(
    conn: asyncpg.Connection, conversation_ids: List[str]
) -> Dict[str, Dict[str, Any]]:
    """Get multiple conversations with joined fanpage and user data.
    Returns dict mapping conversation_id -> conversation details.
    """
    if not conversation_ids:
        return {}

    query = """
        SELECT 
            c.id as conversation_id,
            c.fan_page_id,
            c.facebook_page_scope_user_id,
            fp.name as page_name,
            fp.avatar as page_avatar,
            fp.category as page_category,
            fpsu.user_info
        FROM facebook_conversation_messages c
        JOIN fan_pages fp ON c.fan_page_id = fp.id
        JOIN facebook_page_scope_users fpsu ON c.facebook_page_scope_user_id = fpsu.id
        WHERE c.id = ANY($1::varchar[]) AND c.deleted_at IS NULL
    """
    rows = await execute_async_query(conn, query, conversation_ids)
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        _parse_conversation_json_fields(row)
        cid = row.get("conversation_id")
        if cid:
            out[cid] = row
    return out


async def get_conversation_metadata_with_media(
    conn: asyncpg.Connection, conversation_id: str
) -> Optional[Dict[str, Any]]:
    """
    Fetch conversation core metadata plus page/user avatars with media asset fields.
    Used by agent tooling to build multimodal context.
    """
    query = """
        SELECT 
            fcm.id,
            fcm.fan_page_id,
            fcm.facebook_page_scope_user_id,
            fcm.ad_context,
            fp.name as page_name,
            fp.avatar as page_avatar,
            fp.category as page_category,
            fp.fan_count as page_fan_count,
            fp.followers_count as page_followers_count,
            fp.rating_count as page_rating_count,
            fp.overall_star_rating as page_overall_star_rating,
            fp.about as page_about,
            fp.description as page_description,
            fp.link as page_link,
            fp.website as page_website,
            fp.phone as page_phone,
            fp.emails as page_emails,
            fp.location as page_location,
            fp.cover as page_cover,
            fp.hours as page_hours,
            fp.is_verified as page_is_verified,
            fp.created_at as page_created_at,
            fp.updated_at as page_updated_at,
            page_avatar_asset.id AS page_avatar_media_id,
            page_avatar_asset.s3_url AS page_avatar_s3_url,
            page_avatar_asset.status AS page_avatar_s3_status,
            page_avatar_asset.retention_policy AS page_avatar_retention_policy,
            page_avatar_asset.expires_at AS page_avatar_expires_at,
            fpsu.user_info,
            user_avatar_asset.id AS user_avatar_media_id,
            user_avatar_asset.s3_url AS user_avatar_s3_url,
            user_avatar_asset.status AS user_avatar_s3_status,
            user_avatar_asset.retention_policy AS user_avatar_retention_policy,
            user_avatar_asset.expires_at AS user_avatar_expires_at
        FROM facebook_conversation_messages fcm
        JOIN fan_pages fp ON fcm.fan_page_id = fp.id
        JOIN facebook_page_scope_users fpsu ON fcm.facebook_page_scope_user_id = fpsu.id
        LEFT JOIN media_assets page_avatar_asset
            ON page_avatar_asset.fb_owner_type = 'fan_page'
           AND page_avatar_asset.fb_owner_id::text = fp.id::text
           AND page_avatar_asset.fb_field_name = 'avatar'
           AND page_avatar_asset.source_type = 'facebook_mirror'
        LEFT JOIN media_assets user_avatar_asset
            ON user_avatar_asset.fb_owner_type = 'page_scope_user'
           AND user_avatar_asset.fb_owner_id::text = fpsu.id::text
           AND user_avatar_asset.fb_field_name = 'profile_pic'
           AND user_avatar_asset.source_type = 'facebook_mirror'
        WHERE fcm.id = $1 AND fcm.deleted_at IS NULL
    """

    result = await execute_async_single(conn, query, conversation_id)
    if not result:
        return None
    return result


async def list_conversations_by_page_ids(
    conn: asyncpg.Connection,
    page_ids: List[str],
    limit: int = 20,
    cursor: Optional[Tuple[int, str]] = None,
) -> Tuple[List[asyncpg.Record], bool, Optional[Tuple[int, str]]]:
    """Cursor-based retrieval of facebook conversations for multiple pages."""
    limit = max(1, min(limit, 100))
    params: List[Any] = [page_ids]
    param_idx = 2

    cursor_condition = ""
    if cursor:
        # Cursor is (latest_message_facebook_time, conversation_id)
        # Use COALESCE to handle NULL latest_message_facebook_time (conversations without messages)
        cursor_condition = f" AND (COALESCE(c.latest_message_facebook_time, 0), c.id) < (${param_idx}, ${param_idx + 1})"
        params.extend([cursor[0], cursor[1]])
        param_idx += 2

    params.append(limit + 1)
    limit_placeholder = f"${param_idx}"

    select_query = f"""
        SELECT 
            c.id as conversation_id,
            c.fan_page_id,
            c.facebook_page_scope_user_id,
            c.latest_message_id,
            c.latest_message_is_from_page,
            c.latest_message_facebook_time,
            c.participants_snapshot,
            c.ad_context,
            c.user_seen_at,
            c.created_at as conversation_created_at,
            c.updated_at as conversation_updated_at,
            fp.name as page_name,
            fp.avatar as page_avatar,
            fp.category as page_category,
            fpsu.user_info,
            counts.total_messages,
            counts.unread_count,
            c.mark_as_read,
            lm.id as latest_message_id,
            lm.conversation_id as latest_message_conversation_id,
            lm.is_echo as latest_message_is_echo,
            lm.text as latest_message_text,
            lm.photo_url as latest_message_photo_url,
            lm.video_url as latest_message_video_url,
            lm.audio_url as latest_message_audio_url,
            lm.template_data as latest_message_template_data,
            lm.metadata as latest_message_metadata,
            lm.reply_to_message_id as latest_message_reply_to_message_id,
            lm.facebook_timestamp as latest_message_facebook_timestamp,
            lm.created_at as latest_message_created_at,
            lm.updated_at as latest_message_updated_at
        FROM facebook_conversation_messages c
        JOIN fan_pages fp ON c.fan_page_id = fp.id
        JOIN facebook_page_scope_users fpsu ON c.facebook_page_scope_user_id = fpsu.id
        LEFT JOIN LATERAL (
            SELECT 
                COUNT(*) as total_messages,
                COUNT(*) FILTER (WHERE NOT is_echo AND page_seen_at IS NULL) as unread_count
            FROM messages m
            WHERE m.conversation_id = c.id
        ) counts ON true
        LEFT JOIN LATERAL (
            SELECT *
            FROM messages m
            WHERE m.conversation_id = c.id
              AND m.deleted_at IS NULL
            ORDER BY COALESCE(m.facebook_timestamp, m.created_at * 1000) DESC, m.id DESC
            LIMIT 1
        ) lm ON true
        WHERE c.fan_page_id = ANY($1) AND c.deleted_at IS NULL
        {cursor_condition}
        ORDER BY COALESCE(c.latest_message_facebook_time, 0) DESC, c.id DESC
        LIMIT {limit_placeholder}
    """

    rows = await execute_async_query(conn, select_query, *params)

    # Parse JSON fields
    for row in rows:
        _parse_conversation_json_fields(row)
        _parse_json_field(row, "latest_message_template_data")
        _parse_json_field(row, "latest_message_metadata")

    has_more = len(rows) > limit
    next_cursor_tuple = None
    if has_more:
        last_row = rows[limit - 1]
        # Use latest_message_facebook_time for cursor (or 0 if NULL)
        next_cursor_tuple = (
            last_row.get("latest_message_facebook_time") or 0,
            last_row["conversation_id"],
        )
        rows = rows[:limit]

    return rows, has_more, next_cursor_tuple


# ================================================================
# CONVERSATION CREATION & UPDATE
# ================================================================


async def create_conversation(
    conn: asyncpg.Connection,
    conversation_id: str,
    fan_page_id: str,
    facebook_page_scope_user_id: str,
    participants_snapshot: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Create a new conversation record and return hydrated data.

    Uses ON CONFLICT to handle race conditions gracefully.
    """
    current_time = get_current_timestamp()
    participants_json = (
        json.dumps(participants_snapshot) if participants_snapshot is not None else "[]"
    )

    insert_query = """
        INSERT INTO facebook_conversation_messages (
            id,
            fan_page_id,
            facebook_page_scope_user_id,
            participants_snapshot,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4::jsonb, $5, $5)
        ON CONFLICT (id) DO UPDATE SET
            participants_snapshot = EXCLUDED.participants_snapshot,
            updated_at = EXCLUDED.updated_at
        RETURNING id
    """
    await execute_async_returning(
        conn,
        insert_query,
        conversation_id,
        fan_page_id,
        facebook_page_scope_user_id,
        participants_json,
        current_time,
    )

    return await get_conversation_by_participants(
        conn, fan_page_id, facebook_page_scope_user_id
    )


async def update_conversation_after_message(
    conn: asyncpg.Connection,
    conversation_id: str,
    message_id: str,
    is_echo: bool,
    facebook_timestamp: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Update latest message metadata on the conversation after inserting a message."""
    current_time = get_current_timestamp()
    effective_timestamp = facebook_timestamp or current_time

    query = """
        UPDATE facebook_conversation_messages
        SET
            latest_message_id = $2,
            latest_message_is_from_page = $3,
            latest_message_facebook_time = $4,
            updated_at = $5
        WHERE id = $1
        RETURNING
            id,
            latest_message_id,
            latest_message_is_from_page,
            latest_message_facebook_time,
            updated_at
    """

    return await execute_async_returning(
        conn,
        query,
        conversation_id,
        message_id,
        is_echo,
        effective_timestamp,
        current_time,
    )


async def refresh_conversation_latest_message(
    conn: asyncpg.Connection,
    conversation_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Refresh latest message metadata on the conversation by querying the actual latest message from database.

    This is useful after bulk sync operations to ensure latest_message_* fields are accurate.
    Finds the message with the highest facebook_timestamp (or created_at if facebook_timestamp is NULL).
    """
    # Query the latest message by facebook_timestamp (prefer facebook_timestamp, fallback to created_at)
    query = """
        SELECT 
            id,
            is_echo,
            COALESCE(facebook_timestamp, created_at * 1000) as effective_timestamp
        FROM messages
        WHERE conversation_id = $1
          AND deleted_at IS NULL
        ORDER BY 
            COALESCE(facebook_timestamp, created_at * 1000) DESC,
            id DESC
        LIMIT 1
    """

    latest_message = await execute_async_single(conn, query, conversation_id)

    if not latest_message:
        # No messages found, return None
        return None

    # Update conversation with the latest message
    return await update_conversation_after_message(
        conn=conn,
        conversation_id=conversation_id,
        message_id=latest_message["id"],
        is_echo=latest_message["is_echo"],
        facebook_timestamp=latest_message.get("effective_timestamp"),
    )


# ================================================================
# READ STATE MANAGEMENT
# ================================================================


async def mark_page_messages_seen_by_user(
    conn: asyncpg.Connection,
    conversation_id: str,
    watermark: int,
) -> Optional[Dict[str, Any]]:
    """
    Mark conversation as seen by user based on Facebook webhook watermark.

    Note: We only update user_seen_at at conversation level since Facebook
    doesn't provide specific message IDs - just a watermark timestamp.
    """
    if not watermark:
        return None

    current_time = get_current_timestamp()

    # Update conversation's user_seen_at with the watermark timestamp
    conversation_update_query = """
        UPDATE facebook_conversation_messages
        SET
            user_seen_at = GREATEST($2, COALESCE(user_seen_at, 0)),
            updated_at = $3
        WHERE id = $1
        RETURNING
            user_seen_at,
            latest_message_id,
            latest_message_is_from_page,
            latest_message_facebook_time,
            updated_at
    """

    conversation_state = await execute_async_returning(
        conn,
        conversation_update_query,
        conversation_id,
        watermark,
        current_time,
    )

    if not conversation_state:
        return None

    conversation_state["last_watermark"] = watermark
    return conversation_state


async def mark_conversation_messages_as_seen(
    conn: asyncpg.Connection,
    conversation_id: str,
) -> Dict[str, Any]:
    """
    Mark all user messages in a conversation as seen by the page.
    Sets page_seen_at to current timestamp for all unread user messages.
    """
    current_time = get_current_timestamp()

    message_query = """
        UPDATE messages
        SET page_seen_at = $2
        WHERE conversation_id = $1
          AND is_echo = FALSE
          AND page_seen_at IS NULL
    """
    await execute_async_query(conn, message_query, conversation_id, current_time)

    # Update conversation cursor
    conv_query = """
        UPDATE facebook_conversation_messages 
        SET 
            page_last_seen_message_id = latest_message_id,
            page_last_seen_at = $2,
            updated_at = $3
        WHERE id = $1 AND deleted_at IS NULL
        RETURNING id
    """
    await execute_async_returning(
        conn,
        conv_query,
        conversation_id,
        current_time,
        current_time,
    )

    return await get_conversation_with_details(conn, conversation_id)


async def update_conversation_mark_as_read(
    conn: asyncpg.Connection,
    conversation_id: str,
    mark_as_read: bool,
) -> Dict[str, Any]:
    """
    Toggle mark_as_read boolean status for a conversation (UX feature).
    This is separate from marking messages as seen (page_seen_at).
    """
    current_time = get_current_timestamp()

    query = """
        UPDATE facebook_conversation_messages 
        SET 
            mark_as_read = $2,
            updated_at = $3
        WHERE id = $1 AND deleted_at IS NULL
        RETURNING id
    """
    await execute_async_returning(
        conn, query, conversation_id, mark_as_read, current_time
    )

    return await get_conversation_with_details(conn, conversation_id)


async def update_conversation_ad_context(
    conn: asyncpg.Connection,
    conversation_id: str,
    ad_context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Update ad_context for a conversation.

    Only updates if ad_context is not already set (COALESCE keeps existing value).
    This ensures we preserve the first ad context from when conversation was created.

    Args:
        conn: Database connection
        conversation_id: Conversation ID
        ad_context: Ad context data from webhook referral
            {ad_id, source, type, ad_title, photo_url, video_url, post_id, product_id}

    Returns:
        Updated conversation record or None if not found
    """
    current_time = get_current_timestamp()

    query = """
        UPDATE facebook_conversation_messages
        SET ad_context = COALESCE(ad_context, $2::jsonb),
            updated_at = $3
        WHERE id = $1 AND deleted_at IS NULL
        RETURNING id, ad_context
    """

    return await execute_async_returning(
        conn, query, conversation_id, json.dumps(ad_context), current_time
    )


# ================================================================
# HELPERS
# ================================================================


def _parse_json_field(data: Dict[str, Any], field: str) -> None:
    """Parse a JSON string field to dictionary in-place."""
    if data.get(field) and isinstance(data[field], str):
        try:
            data[field] = json.loads(data[field])
        except (json.JSONDecodeError, TypeError):
            data[field] = None


def _parse_conversation_json_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse common JSON fields in conversation data."""
    _parse_json_field(data, "user_info")
    _parse_json_field(data, "ad_context")

    if data.get("participants_snapshot") and isinstance(
        data["participants_snapshot"], str
    ):
        try:
            data["participants_snapshot"] = json.loads(data["participants_snapshot"])
        except (json.JSONDecodeError, TypeError):
            data["participants_snapshot"] = []

    return data
