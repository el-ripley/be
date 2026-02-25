"""
Notification SQL query functions.
Handles CRUD for in-app notifications (persistence + read tracking).
"""

import json
from typing import Any, Dict, List, Optional

import asyncpg

from ..executor import (
    execute_async_query,
    execute_async_returning,
    execute_async_scalar,
    execute_async_command,
)
from ..utils import get_current_timestamp_ms


def _normalize_notification_row(
    row: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Normalize notification row: UUIDs as str, metadata as dict."""
    if not row:
        return None
    out = dict(row)
    for field in ("id", "reference_id"):
        if out.get(field) is not None:
            out[field] = str(out[field])
    # Ensure metadata is a dict (DB/jsonb can sometimes return str)
    meta = out.get("metadata")
    if isinstance(meta, str):
        try:
            out["metadata"] = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            out["metadata"] = {}
    elif meta is not None and not isinstance(meta, dict):
        out["metadata"] = {}
    return out


async def insert_notification(
    conn: asyncpg.Connection,
    owner_user_id: str,
    type: str,
    title: str,
    body: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Insert a notification and return the full row."""
    now = get_current_timestamp_ms()
    meta = metadata if metadata is not None else {}
    meta_json = json.dumps(meta)
    query = """
        INSERT INTO notifications (
            owner_user_id, type, title, body,
            reference_type, reference_id, metadata,
            is_read, read_at, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6::uuid, $7::jsonb, FALSE, NULL, $8)
        RETURNING id, owner_user_id, type, title, body,
                  reference_type, reference_id, metadata,
                  is_read, read_at, created_at
    """
    row = await execute_async_returning(
        conn,
        query,
        owner_user_id,
        type,
        title,
        body,
        reference_type,
        reference_id,
        meta_json,
        now,
    )
    return _normalize_notification_row(row) or row  # type: ignore[return-value]


async def get_notifications(
    conn: asyncpg.Connection,
    owner_user_id: str,
    is_read: Optional[bool] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Get paginated notifications for a user, newest first."""
    if is_read is None:
        query = """
            SELECT id, owner_user_id, type, title, body,
                   reference_type, reference_id, metadata,
                   is_read, read_at, created_at
            FROM notifications
            WHERE owner_user_id = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
        """
        rows = await execute_async_query(conn, query, owner_user_id, limit, offset)
    else:
        query = """
            SELECT id, owner_user_id, type, title, body,
                   reference_type, reference_id, metadata,
                   is_read, read_at, created_at
            FROM notifications
            WHERE owner_user_id = $1 AND is_read = $2
            ORDER BY created_at DESC
            LIMIT $3 OFFSET $4
        """
        rows = await execute_async_query(
            conn, query, owner_user_id, is_read, limit, offset
        )
    return [_normalize_notification_row(r) or r for r in rows]


async def count_unread_notifications(
    conn: asyncpg.Connection,
    owner_user_id: str,
) -> int:
    """Count unread notifications for a user."""
    query = """
        SELECT COUNT(*)::int FROM notifications
        WHERE owner_user_id = $1 AND is_read = FALSE
    """
    val = await execute_async_scalar(conn, query, owner_user_id)
    return val or 0


async def mark_notification_read(
    conn: asyncpg.Connection,
    notification_id: str,
    owner_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Mark a single notification as read. Returns updated row or None if not found."""
    now = get_current_timestamp_ms()
    query = """
        UPDATE notifications
        SET is_read = TRUE, read_at = $1
        WHERE id = $2::uuid AND owner_user_id = $3
        RETURNING id, owner_user_id, type, title, body,
                  reference_type, reference_id, metadata,
                  is_read, read_at, created_at
    """
    row = await execute_async_returning(
        conn, query, now, notification_id, owner_user_id
    )
    return _normalize_notification_row(row)


async def mark_all_notifications_read(
    conn: asyncpg.Connection,
    owner_user_id: str,
) -> None:
    """Mark all notifications for a user as read."""
    now = get_current_timestamp_ms()
    query = """
        UPDATE notifications
        SET is_read = TRUE, read_at = $1
        WHERE owner_user_id = $2 AND is_read = FALSE
    """
    await execute_async_command(conn, query, now, owner_user_id)
