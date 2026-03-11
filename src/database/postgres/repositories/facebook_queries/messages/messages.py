"""
Facebook Message Database Operations.

Handles CRUD operations for individual messages within conversations.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from src.database.postgres.executor import execute_async_query, execute_async_returning
from src.database.postgres.utils import get_current_timestamp

# ================================================================
# MESSAGE CREATION
# ================================================================


async def create_message(
    conn: asyncpg.Connection,
    message_id: str,
    conversation_id: str,
    is_echo: bool = False,
    text: Optional[str] = None,
    photo_url: Optional[str] = None,
    video_url: Optional[str] = None,
    audio_url: Optional[str] = None,
    template_data: Optional[Dict[str, Any]] = None,
    facebook_timestamp: Optional[int] = None,
    created_at: Optional[int] = None,
    updated_at: Optional[int] = None,
    page_seen_at: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    reply_to_message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new message record and return full message data.

    Uses ON CONFLICT to handle duplicate messages gracefully.
    """
    current_time = get_current_timestamp()

    template_data_json = None
    if template_data:
        template_data_json = json.dumps(template_data)

    metadata_json = None
    if metadata:
        metadata_json = json.dumps(metadata)

    query = """
        INSERT INTO messages (
            id, conversation_id, is_echo, text,
            photo_url, video_url, audio_url, template_data,
            facebook_timestamp, page_seen_at, metadata, reply_to_message_id,
            created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        ON CONFLICT (id) DO UPDATE SET
            text = EXCLUDED.text,
            photo_url = EXCLUDED.photo_url,
            video_url = EXCLUDED.video_url,
            audio_url = EXCLUDED.audio_url,
            template_data = EXCLUDED.template_data,
            page_seen_at = COALESCE(EXCLUDED.page_seen_at, messages.page_seen_at),
            metadata = COALESCE(EXCLUDED.metadata, messages.metadata),
            reply_to_message_id = COALESCE(EXCLUDED.reply_to_message_id, messages.reply_to_message_id),
            updated_at = EXCLUDED.updated_at
        RETURNING *
    """

    return await execute_async_returning(
        conn,
        query,
        message_id,
        conversation_id,
        is_echo,
        text,
        photo_url,
        video_url,
        audio_url,
        template_data_json,
        facebook_timestamp,
        page_seen_at,
        metadata_json,
        reply_to_message_id,
        created_at or current_time,
        updated_at or current_time,
    )


# ================================================================
# MESSAGE RETRIEVAL
# ================================================================


async def list_messages_by_conversation_id(
    conn: asyncpg.Connection,
    conversation_id: str,
    limit: int = 20,
    cursor: Optional[Tuple[int, str]] = None,
) -> Tuple[List[asyncpg.Record], bool, Optional[Tuple[int, str]]]:
    """Cursor-based retrieval of messages within a conversation."""
    limit = max(1, min(limit, 100))
    params: List[Any] = [conversation_id]
    param_idx = 2

    # Prefer Facebook timestamp for correct chronological ordering; fall back to created_at (ms)
    sort_expression = "COALESCE(m.facebook_timestamp, m.created_at * 1000)"

    cursor_condition = ""
    if cursor:
        cursor_condition = (
            f" AND ({sort_expression}, m.id) < (${param_idx}, ${param_idx + 1})"
        )
        params.extend([cursor[0], cursor[1]])
        param_idx += 2

    params.append(limit + 1)
    limit_placeholder = f"${param_idx}"

    select_query = f"""
        SELECT 
            m.id,
            m.conversation_id,
            m.is_echo,
            m.text,
            m.photo_url,
            m.video_url,
            m.audio_url,
            m.template_data,
            m.metadata,
            m.reply_to_message_id,
            m.facebook_timestamp,
            m.created_at,
            m.updated_at,
            {sort_expression} AS sort_value,
            photo_asset.id AS photo_media_id,
            photo_asset.s3_url AS photo_s3_url,
            photo_asset.status AS photo_s3_status,
            photo_asset.retention_policy AS photo_retention_policy,
            photo_asset.expires_at AS photo_expires_at
        FROM messages m
        LEFT JOIN media_assets photo_asset
            ON photo_asset.source_type = 'facebook_mirror'
           AND photo_asset.fb_owner_type = 'message'
           AND photo_asset.fb_owner_id::text = m.id::text
           AND photo_asset.fb_field_name = 'photo_url'
        WHERE m.conversation_id = $1
        {cursor_condition}
        ORDER BY {sort_expression} DESC, m.id DESC
        LIMIT {limit_placeholder}
    """

    messages = await execute_async_query(conn, select_query, *params)

    has_more = len(messages) > limit
    next_cursor_tuple = None
    if has_more:
        last_row = messages[limit - 1]
        next_cursor_tuple = (last_row["sort_value"], last_row["id"])
        messages = messages[:limit]

    # Process each message and strip internal sorting helper
    for message in messages:
        _process_message_row(message)
        if "sort_value" in message:
            del message["sort_value"]

    return messages, has_more, next_cursor_tuple


async def list_messages_by_conversation_id_paginated(
    conn: asyncpg.Connection,
    conversation_id: str,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[asyncpg.Record], int, bool]:
    """
    Offset-based retrieval of messages within a conversation.

    Returns (messages, total_count, has_next_page).
    Messages are returned in descending chronological order (newest first).
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    # Prefer Facebook timestamp for correct chronological ordering
    sort_expression = "COALESCE(m.facebook_timestamp, m.created_at * 1000)"

    # Count total messages
    count_query = """
        SELECT COUNT(*) as total
        FROM messages m
        WHERE m.conversation_id = $1
    """
    count_result = await execute_async_query(conn, count_query, conversation_id)
    total_count = count_result[0]["total"] if count_result else 0

    # Fetch paginated messages (descending order - newest first)
    select_query = f"""
        SELECT 
            m.id,
            m.conversation_id,
            m.is_echo,
            m.text,
            m.photo_url,
            m.video_url,
            m.audio_url,
            m.template_data,
            m.metadata,
            m.reply_to_message_id,
            m.facebook_timestamp,
            m.created_at,
            m.updated_at,
            photo_asset.id AS photo_media_id,
            photo_asset.s3_url AS photo_s3_url,
            photo_asset.status AS photo_s3_status,
            photo_asset.retention_policy AS photo_retention_policy,
            photo_asset.expires_at AS photo_expires_at
        FROM messages m
        LEFT JOIN media_assets photo_asset
            ON photo_asset.source_type = 'facebook_mirror'
           AND photo_asset.fb_owner_type = 'message'
           AND photo_asset.fb_owner_id::text = m.id::text
           AND photo_asset.fb_field_name = 'photo_url'
        WHERE m.conversation_id = $1
        ORDER BY {sort_expression} DESC, m.id DESC
        LIMIT $2 OFFSET $3
    """

    messages = await execute_async_query(
        conn, select_query, conversation_id, page_size, offset
    )

    # Process each message
    for message in messages:
        _process_message_row(message)

    has_next_page = (offset + len(messages)) < total_count

    return list(messages), total_count, has_next_page


# ================================================================
# HELPERS
# ================================================================


def _media_is_active(media: Dict[str, Any]) -> bool:
    """Check if media asset is ready and not expired."""
    if not media:
        return False

    status = media.get("status")
    expires_at = media.get("expires_at")

    if status != "ready":
        return False
    if expires_at is None:
        return True

    try:
        return int(expires_at) > get_current_timestamp()
    except (TypeError, ValueError):
        return False


def _process_message_row(message: Dict[str, Any]) -> None:
    """Process a message row: parse JSON and handle media assets."""
    # Parse template_data JSON
    if message.get("template_data") and isinstance(message["template_data"], str):
        try:
            message["template_data"] = json.loads(message["template_data"])
        except (json.JSONDecodeError, TypeError):
            message["template_data"] = None

    # Convert UUID to string if present
    photo_media_id_raw = message.get("photo_media_id")
    photo_media_id = None
    if photo_media_id_raw is not None:
        photo_media_id = (
            str(photo_media_id_raw)
            if hasattr(photo_media_id_raw, "__str__")
            else photo_media_id_raw
        )

    # Build photo_media object
    photo_media = {
        "id": photo_media_id,
        "s3_url": message.get("photo_s3_url"),
        "status": message.get("photo_s3_status"),
        "retention_policy": message.get("photo_retention_policy"),
        "expires_at": message.get("photo_expires_at"),
        "original_url": message.get("photo_url"),
    }
    message["photo_media"] = photo_media

    # Use S3 URL if available and active
    if photo_media["s3_url"] and _media_is_active(photo_media):
        message["photo_url"] = photo_media["s3_url"]

    # Clean up extra keys
    for extra_key in (
        "photo_media_id",
        "photo_s3_url",
        "photo_s3_status",
        "photo_retention_policy",
        "photo_expires_at",
    ):
        if extra_key in message:
            del message[extra_key]


# ================================================================
# BATCH OPERATIONS
# ================================================================


async def batch_create_messages(
    conn: asyncpg.Connection, messages_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Batch insert multiple messages with ON CONFLICT handling.

    This is much more efficient than individual inserts for syncing message history.

    Args:
        conn: Database connection
        messages_data: List of message dicts with keys:
            - message_id: str (required)
            - conversation_id: str (required)
            - is_echo: bool (default: False)
            - text: Optional[str]
            - photo_url: Optional[str]
            - video_url: Optional[str]
            - audio_url: Optional[str]
            - template_data: Optional[Dict[str, Any]]
            - facebook_timestamp: Optional[int]
            - page_seen_at: Optional[int]
            - reply_to_message_id: Optional[str]
            - created_at: Optional[int]
            - updated_at: Optional[int]

    Returns:
        List of created/updated message records
    """
    if not messages_data:
        return []

    current_time = get_current_timestamp()
    values_placeholders = []
    all_params: List[Any] = []
    param_index = 1

    for message in messages_data:
        placeholders = []
        for _ in range(13):  # 13 fields
            placeholders.append(f"${param_index}")
            param_index += 1
        values_placeholders.append(f"({', '.join(placeholders)})")

        # Serialize template_data to JSON string if present
        template_data = message.get("template_data")
        template_data_json = None
        if template_data:
            template_data_json = json.dumps(template_data)

        all_params.extend(
            [
                message.get("message_id"),
                message.get("conversation_id"),
                message.get("is_echo", False),
                message.get("text"),
                message.get("photo_url"),
                message.get("video_url"),
                message.get("audio_url"),
                template_data_json,
                message.get("facebook_timestamp"),
                message.get("page_seen_at"),
                message.get("reply_to_message_id"),
                message.get("created_at", current_time),
                message.get("updated_at", current_time),
            ]
        )

    query = f"""
        INSERT INTO messages (
            id, conversation_id, is_echo, text, 
            photo_url, video_url, audio_url, template_data, 
            facebook_timestamp, page_seen_at, reply_to_message_id,
            created_at, updated_at
        )
        VALUES {', '.join(values_placeholders)}
        ON CONFLICT (id) DO UPDATE SET
            text = EXCLUDED.text,
            photo_url = EXCLUDED.photo_url,
            video_url = EXCLUDED.video_url,
            audio_url = EXCLUDED.audio_url,
            template_data = EXCLUDED.template_data,
            page_seen_at = COALESCE(EXCLUDED.page_seen_at, messages.page_seen_at),
            reply_to_message_id = COALESCE(EXCLUDED.reply_to_message_id, messages.reply_to_message_id),
            updated_at = EXCLUDED.updated_at
        RETURNING *
    """

    results = await execute_async_query(conn, query, *all_params)

    # Process each message row (parse JSON, etc.)
    for message in results:
        _process_message_row(message)

    return results
