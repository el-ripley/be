"""
Facebook Post Comment Sync State Repository.

Tracks per-post progress for syncing comments.
"""

from typing import Any, Dict, List, Optional

import asyncpg

from src.database.postgres.executor import (
    execute_async_query,
    execute_async_returning,
    execute_async_single,
)
from src.database.postgres.utils import get_current_timestamp


async def get_comment_sync_state(
    conn: asyncpg.Connection,
    post_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Get current comment sync state for a post.

    Returns:
        Dict with sync state or None if not initialized.
    """
    query = """
        SELECT
            id,
            post_id,
            fan_page_id,
            comments_cursor,
            total_synced_root_comments,
            total_synced_comments,
            status,
            last_sync_at,
            created_at,
            updated_at
        FROM facebook_post_comment_sync_states
        WHERE post_id = $1
        LIMIT 1
    """
    return await execute_async_single(conn, query, post_id)


async def upsert_comment_sync_state(
    conn: asyncpg.Connection,
    *,
    post_id: str,
    fan_page_id: str,
    comments_cursor: Optional[str],
    total_synced_root_comments: int,
    total_synced_comments: int,
    status: str,
    last_sync_at: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create or update comment sync state for a post.

    This function is idempotent and safe to call after each sync batch.
    """
    current_time = get_current_timestamp()
    effective_last_sync_at = last_sync_at or current_time

    query = """
        INSERT INTO facebook_post_comment_sync_states (
            post_id,
            fan_page_id,
            comments_cursor,
            total_synced_root_comments,
            total_synced_comments,
            status,
            last_sync_at,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
        ON CONFLICT (post_id) DO UPDATE SET
            comments_cursor = EXCLUDED.comments_cursor,
            total_synced_root_comments = EXCLUDED.total_synced_root_comments,
            total_synced_comments = EXCLUDED.total_synced_comments,
            status = EXCLUDED.status,
            last_sync_at = EXCLUDED.last_sync_at,
            updated_at = EXCLUDED.updated_at
        RETURNING
            id,
            post_id,
            fan_page_id,
            comments_cursor,
            total_synced_root_comments,
            total_synced_comments,
            status,
            last_sync_at,
            created_at,
            updated_at
    """

    return await execute_async_returning(
        conn,
        query,
        post_id,
        fan_page_id,
        comments_cursor,
        total_synced_root_comments,
        total_synced_comments,
        status,
        effective_last_sync_at,
        current_time,
    )


async def get_posts_needing_comment_sync(
    conn: asyncpg.Connection,
    fan_page_id: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    Get posts that need comment sync (no sync state or incomplete).

    Returns posts ordered by facebook_created_time DESC (newest first).
    """
    query = """
        SELECT p.id, p.fan_page_id, p.message, p.facebook_created_time,
               p.comment_count, pcs.status AS sync_status,
               pcs.total_synced_comments
        FROM posts p
        LEFT JOIN facebook_post_comment_sync_states pcs ON p.id = pcs.post_id
        WHERE p.fan_page_id = $1
          AND (pcs.status IS NULL OR pcs.status != 'completed')
        ORDER BY p.facebook_created_time DESC NULLS LAST
        LIMIT $2
    """
    return await execute_async_query(conn, query, fan_page_id, limit)


async def reset_comment_sync_state(
    conn: asyncpg.Connection,
    post_id: str,
    *,
    clear_totals: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Internal helper to reset comment sync cursor for a post.

    By default it only clears comments_cursor and sets status to 'idle' while
    preserving accumulated totals. When clear_totals=True, counters are reset.
    """
    current_time = get_current_timestamp()

    if clear_totals:
        query = """
            UPDATE facebook_post_comment_sync_states
            SET
                comments_cursor = NULL,
                total_synced_root_comments = 0,
                total_synced_comments = 0,
                status = 'idle',
                last_sync_at = $2,
                updated_at = $2
            WHERE post_id = $1
            RETURNING
                id,
                post_id,
                fan_page_id,
                comments_cursor,
                total_synced_root_comments,
                total_synced_comments,
                status,
                last_sync_at,
                created_at,
                updated_at
        """
        return await execute_async_returning(conn, query, post_id, current_time)

    query = """
        UPDATE facebook_post_comment_sync_states
        SET
            comments_cursor = NULL,
            status = 'idle',
            last_sync_at = $2,
            updated_at = $2
        WHERE post_id = $1
        RETURNING
            id,
            post_id,
            fan_page_id,
            comments_cursor,
            total_synced_root_comments,
            total_synced_comments,
            status,
            last_sync_at,
            created_at,
            updated_at
    """
    return await execute_async_returning(conn, query, post_id, current_time)
