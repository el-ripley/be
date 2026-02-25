"""
Facebook Inbox Sync State Repository.

Tracks per-page progress for syncing Messenger inbox conversations and messages.
"""

from typing import Optional, Dict, Any

import asyncpg

from src.database.postgres.executor import execute_async_single, execute_async_returning
from src.database.postgres.utils import get_current_timestamp


async def get_sync_state(
    conn: asyncpg.Connection,
    fan_page_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Get current inbox sync state for a page.

    Returns:
        Dict with sync state or None if not initialized.
    """
    query = """
        SELECT
            id,
            fan_page_id,
            fb_cursor,
            total_synced_conversations,
            total_synced_messages,
            status,
            last_sync_at,
            created_at,
            updated_at
        FROM facebook_inbox_sync_states
        WHERE fan_page_id = $1
        LIMIT 1
    """
    return await execute_async_single(conn, query, fan_page_id)


async def upsert_sync_state(
    conn: asyncpg.Connection,
    *,
    fan_page_id: str,
    fb_cursor: Optional[str],
    total_synced_conversations: int,
    total_synced_messages: int,
    status: str,
    last_sync_at: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create or update inbox sync state for a page.

    This function is idempotent and safe to call after each sync batch.
    """
    current_time = get_current_timestamp()
    effective_last_sync_at = last_sync_at or current_time

    query = """
        INSERT INTO facebook_inbox_sync_states (
            fan_page_id,
            fb_cursor,
            total_synced_conversations,
            total_synced_messages,
            status,
            last_sync_at,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
        ON CONFLICT (fan_page_id) DO UPDATE SET
            fb_cursor = EXCLUDED.fb_cursor,
            total_synced_conversations = EXCLUDED.total_synced_conversations,
            total_synced_messages = EXCLUDED.total_synced_messages,
            status = EXCLUDED.status,
            last_sync_at = EXCLUDED.last_sync_at,
            updated_at = EXCLUDED.updated_at
        RETURNING
            id,
            fan_page_id,
            fb_cursor,
            total_synced_conversations,
            total_synced_messages,
            status,
            last_sync_at,
            created_at,
            updated_at
    """

    return await execute_async_returning(
        conn,
        query,
        fan_page_id,
        fb_cursor,
        total_synced_conversations,
        total_synced_messages,
        status,
        effective_last_sync_at,
        current_time,
    )


async def reset_sync_state(
    conn: asyncpg.Connection,
    fan_page_id: str,
    *,
    clear_totals: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Internal helper to reset sync cursor for a page.

    By default it only clears fb_cursor and sets status to 'idle' while
    preserving accumulated totals. When clear_totals=True, counters are reset.
    """
    current_time = get_current_timestamp()

    if clear_totals:
        query = """
            UPDATE facebook_inbox_sync_states
            SET
                fb_cursor = NULL,
                total_synced_conversations = 0,
                total_synced_messages = 0,
                status = 'idle',
                last_sync_at = $2,
                updated_at = $2
            WHERE fan_page_id = $1
            RETURNING
                id,
                fan_page_id,
                fb_cursor,
                total_synced_conversations,
                total_synced_messages,
                status,
                last_sync_at,
                created_at,
                updated_at
        """
        return await execute_async_returning(conn, query, fan_page_id, current_time)

    query = """
        UPDATE facebook_inbox_sync_states
        SET
            fb_cursor = NULL,
            status = 'idle',
            last_sync_at = $2,
            updated_at = $2
        WHERE fan_page_id = $1
        RETURNING
            id,
            fan_page_id,
            fb_cursor,
            total_synced_conversations,
            total_synced_messages,
            status,
            last_sync_at,
            created_at,
            updated_at
    """
    return await execute_async_returning(conn, query, fan_page_id, current_time)
