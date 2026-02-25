import json
from typing import Optional, Dict, Any, List

import asyncpg

from src.database.postgres.executor import (
    execute_async_single,
    execute_async_returning,
    execute_async_query,
)
from src.database.postgres.utils import get_current_timestamp


async def create_comment(
    conn: asyncpg.Connection,
    comment_id: str,
    post_id: str,
    fan_page_id: str,
    parent_comment_id: Optional[str] = None,
    is_from_page: bool = False,
    facebook_page_scope_user_id: Optional[str] = None,
    message: Optional[str] = None,
    photo_url: Optional[str] = None,
    video_url: Optional[str] = None,
    facebook_created_time: Optional[int] = None,
    like_count: Optional[int] = 0,
    reply_count: Optional[int] = 0,
    is_hidden: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    created_at: Optional[int] = None,
    updated_at: Optional[int] = None,
) -> Dict[str, Any]:
    """Create or update a comment record."""
    current_time = get_current_timestamp()
    metadata_json = None
    if metadata is not None:
        metadata_json = json.dumps(metadata) if isinstance(metadata, dict) else metadata

    query = """
        INSERT INTO comments (id, post_id, fan_page_id, parent_comment_id, is_from_page, facebook_page_scope_user_id, message, photo_url, video_url, facebook_created_time, like_count, reply_count, is_hidden, metadata, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
        ON CONFLICT (id) DO UPDATE SET
            message = EXCLUDED.message,
            photo_url = EXCLUDED.photo_url,
            video_url = EXCLUDED.video_url,
            like_count = EXCLUDED.like_count,
            reply_count = EXCLUDED.reply_count,
            is_hidden = EXCLUDED.is_hidden,
            metadata = COALESCE(EXCLUDED.metadata, comments.metadata),
            updated_at = EXCLUDED.updated_at
        RETURNING *
    """

    return await execute_async_returning(
        conn,
        query,
        comment_id,
        post_id,
        fan_page_id,
        parent_comment_id,
        is_from_page,
        facebook_page_scope_user_id,
        message,
        photo_url,
        video_url,
        facebook_created_time,
        like_count or 0,
        reply_count or 0,
        is_hidden,
        metadata_json,
        created_at or current_time,
        updated_at or current_time,
    )


async def update_comment_visibility(
    conn: asyncpg.Connection,
    comment_id: str,
    is_hidden: bool,
    updated_at: Optional[int] = None,
) -> str:
    """Update comment visibility (hide/unhide)."""
    current_time = get_current_timestamp()

    query = """
        UPDATE comments
        SET is_hidden = $2, updated_at = $3
        WHERE id = $1
        RETURNING id
    """

    result = await execute_async_returning(
        conn, query, comment_id, is_hidden, updated_at or current_time
    )
    return result["id"]


async def update_comment(
    conn: asyncpg.Connection,
    comment_id: str,
    message: Optional[str] = None,
    photo_url: Optional[str] = None,
    video_url: Optional[str] = None,
    updated_at: Optional[int] = None,
) -> Dict[str, Any]:
    """Update comment content (message, photo_url, video_url)."""
    current_time = get_current_timestamp()

    query = """
        UPDATE comments
        SET message = $2, photo_url = $3, video_url = $4, updated_at = $5
        WHERE id = $1
        RETURNING *
    """

    return await execute_async_returning(
        conn,
        query,
        comment_id,
        message,
        photo_url,
        video_url,
        updated_at or current_time,
    )


async def soft_delete_comment(
    conn: asyncpg.Connection, comment_id: str, deleted_at: Optional[int] = None
) -> str:
    """Soft delete a comment."""
    current_time = get_current_timestamp()

    query = """
        UPDATE comments
        SET deleted_at = $2, updated_at = $2
        WHERE id = $1
        RETURNING id
    """

    result = await execute_async_returning(
        conn, query, comment_id, deleted_at or current_time
    )
    return result["id"]


async def get_comment(
    conn: asyncpg.Connection, comment_id: str
) -> Optional[Dict[str, Any]]:
    """Get comment with joined facebook_page_scope_user and page information."""
    query = """
        SELECT
            c.id,
            c.post_id,
            c.fan_page_id,
            c.parent_comment_id,
            c.is_from_page,
            c.facebook_page_scope_user_id,
            c.message,
            c.photo_url,
            c.video_url,
            c.facebook_created_time,
            c.is_hidden,
            c.page_seen_at,
            c.deleted_at,
            c.metadata,
            c.created_at,
            c.updated_at,
            fpsu.user_info,
            fp.name as page_name,
            fp.avatar as page_avatar,
            fp.category as page_category
        FROM comments c
        LEFT JOIN facebook_page_scope_users fpsu ON c.facebook_page_scope_user_id = fpsu.id
        JOIN fan_pages fp ON c.fan_page_id = fp.id
        WHERE c.id = $1
    """

    return await execute_async_single(conn, query, comment_id)


async def mark_comment_as_seen(
    conn: asyncpg.Connection,
    comment_id: str,
    seen_at: Optional[int] = None,
) -> str:
    """Mark a comment as seen by setting page_seen_at timestamp."""
    current_time = seen_at or get_current_timestamp()

    query = """
        UPDATE comments
        SET page_seen_at = $2, updated_at = $2
        WHERE id = $1
        RETURNING id
    """

    result = await execute_async_returning(conn, query, comment_id, current_time)
    return result["id"]


async def mark_comments_as_seen(
    conn: asyncpg.Connection,
    comment_ids: List[str],
    seen_at: Optional[int] = None,
) -> int:
    """Mark multiple comments as seen."""
    if not comment_ids:
        return 0

    current_time = seen_at or get_current_timestamp()

    query = """
        UPDATE comments
        SET page_seen_at = $2, updated_at = $2
        WHERE id = ANY($1)
        RETURNING id
    """

    rows = await execute_async_query(conn, query, comment_ids, current_time)
    return len(rows)


async def ensure_comment_exists(
    conn: asyncpg.Connection,
    comment_id: str,
    post_id: str,
    fan_page_id: str,
    parent_comment_id: Optional[str] = None,
    is_from_page: bool = False,
    facebook_page_scope_user_id: Optional[str] = None,
    message: Optional[str] = None,
    photo_url: Optional[str] = None,
    video_url: Optional[str] = None,
    facebook_created_time: Optional[int] = None,
) -> bool:
    """
    Ensure comment exists in database. If not, create it with available information.
    Returns True if comment already existed, False if it was created.
    """
    existing_comment = await get_comment(conn, comment_id)

    if existing_comment and not existing_comment.get("deleted_at"):
        return True

    await create_comment(
        conn=conn,
        comment_id=comment_id,
        post_id=post_id,
        fan_page_id=fan_page_id,
        parent_comment_id=parent_comment_id,
        is_from_page=is_from_page,
        facebook_page_scope_user_id=facebook_page_scope_user_id,
        message=message,
        photo_url=photo_url,
        video_url=video_url,
        facebook_created_time=facebook_created_time,
    )
    return False


async def batch_create_comments(
    conn: asyncpg.Connection, comments_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Batch create or update multiple comments."""
    if not comments_data:
        return []

    current_time = get_current_timestamp()
    values_placeholders = []
    all_params: List[Any] = []
    param_index = 1

    for comment in comments_data:
        placeholders = []
        for _ in range(16):  # 15 + metadata
            placeholders.append(f"${param_index}")
            param_index += 1
        values_placeholders.append(f"({', '.join(placeholders)})")
        meta = comment.get("metadata")
        meta_json = json.dumps(meta) if isinstance(meta, dict) else meta
        all_params.extend(
            [
                comment.get("comment_id"),
                comment.get("post_id"),
                comment.get("fan_page_id"),
                comment.get("parent_comment_id"),
                comment.get("is_from_page", False),
                comment.get("facebook_page_scope_user_id"),
                comment.get("message"),
                comment.get("photo_url"),
                comment.get("video_url"),
                comment.get("facebook_created_time"),
                comment.get("like_count", 0),
                comment.get("reply_count", 0),
                comment.get("is_hidden", False),
                meta_json,
                comment.get("created_at", current_time),
                comment.get("updated_at", current_time),
            ]
        )

    query = f"""
        INSERT INTO comments (id, post_id, fan_page_id, parent_comment_id, is_from_page, facebook_page_scope_user_id, message, photo_url, video_url, facebook_created_time, like_count, reply_count, is_hidden, metadata, created_at, updated_at)
        VALUES {', '.join(values_placeholders)}
        ON CONFLICT (id) DO UPDATE SET
            message = EXCLUDED.message,
            photo_url = EXCLUDED.photo_url,
            video_url = EXCLUDED.video_url,
            like_count = EXCLUDED.like_count,
            reply_count = EXCLUDED.reply_count,
            is_hidden = EXCLUDED.is_hidden,
            metadata = COALESCE(EXCLUDED.metadata, comments.metadata),
            updated_at = EXCLUDED.updated_at
        RETURNING *
    """

    return await execute_async_query(conn, query, *all_params)
