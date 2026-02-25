"""
Facebook Post Sync State Repository.

Tracks per-page progress for syncing posts from Facebook.
"""

from typing import Optional, Dict, Any

import asyncpg

from src.database.postgres.executor import execute_async_single, execute_async_returning
from src.database.postgres.utils import get_current_timestamp


async def get_post_sync_state(
    conn: asyncpg.Connection,
    fan_page_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Get current post sync state for a page.

    Returns:
        Dict with sync state or None if not initialized.
    """
    query = """
        SELECT
            id,
            fan_page_id,
            posts_cursor,
            total_synced_posts,
            status,
            last_sync_at,
            created_at,
            updated_at
        FROM facebook_post_sync_states
        WHERE fan_page_id = $1
        LIMIT 1
    """
    return await execute_async_single(conn, query, fan_page_id)


async def upsert_post_sync_state(
    conn: asyncpg.Connection,
    *,
    fan_page_id: str,
    posts_cursor: Optional[str],
    total_synced_posts: int,
    status: str,
    last_sync_at: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create or update post sync state for a page.

    This function is idempotent and safe to call after each sync batch.
    """
    current_time = get_current_timestamp()
    effective_last_sync_at = last_sync_at or current_time

    query = """
        INSERT INTO facebook_post_sync_states (
            fan_page_id,
            posts_cursor,
            total_synced_posts,
            status,
            last_sync_at,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $6)
        ON CONFLICT (fan_page_id) DO UPDATE SET
            posts_cursor = EXCLUDED.posts_cursor,
            total_synced_posts = EXCLUDED.total_synced_posts,
            status = EXCLUDED.status,
            last_sync_at = EXCLUDED.last_sync_at,
            updated_at = EXCLUDED.updated_at
        RETURNING
            id,
            fan_page_id,
            posts_cursor,
            total_synced_posts,
            status,
            last_sync_at,
            created_at,
            updated_at
    """

    return await execute_async_returning(
        conn,
        query,
        fan_page_id,
        posts_cursor,
        total_synced_posts,
        status,
        effective_last_sync_at,
        current_time,
    )


async def reset_post_sync_state(
    conn: asyncpg.Connection,
    fan_page_id: str,
    *,
    clear_totals: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Internal helper to reset post sync cursor for a page.

    By default it only clears posts_cursor and sets status to 'idle' while
    preserving accumulated totals. When clear_totals=True, counters are reset.
    """
    current_time = get_current_timestamp()

    if clear_totals:
        query = """
            UPDATE facebook_post_sync_states
            SET
                posts_cursor = NULL,
                total_synced_posts = 0,
                status = 'idle',
                last_sync_at = $2,
                updated_at = $2
            WHERE fan_page_id = $1
            RETURNING
                id,
                fan_page_id,
                posts_cursor,
                total_synced_posts,
                status,
                last_sync_at,
                created_at,
                updated_at
        """
        return await execute_async_returning(conn, query, fan_page_id, current_time)

    query = """
        UPDATE facebook_post_sync_states
        SET
            posts_cursor = NULL,
            status = 'idle',
            last_sync_at = $2,
            updated_at = $2
        WHERE fan_page_id = $1
        RETURNING
            id,
            fan_page_id,
            posts_cursor,
            total_synced_posts,
            status,
            last_sync_at,
            created_at,
            updated_at
    """
    return await execute_async_returning(conn, query, fan_page_id, current_time)

