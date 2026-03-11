"""
User Storage Quotas SQL query functions.

Handles quota tracking for permanent media assets.
"""

from typing import Any, Dict, Optional, Tuple

import asyncpg

from ..executor import execute_async_returning, execute_async_single
from ..utils import get_current_timestamp_ms


async def get_user_storage_quota(
    conn: asyncpg.Connection,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Get user storage quota record.

    Args:
        conn: Database connection
        user_id: User ID

    Returns:
        Quota record or None if not found
    """
    query = """
        SELECT *
        FROM user_storage_quotas
        WHERE user_id = $1
    """
    return await execute_async_single(conn, query, user_id)


async def create_or_update_user_storage_quota(
    conn: asyncpg.Connection,
    user_id: str,
    size_delta: int,
) -> Dict[str, Any]:
    """
    Create or update user storage quota by adding size_delta.
    Creates quota record if it doesn't exist.

    Args:
        conn: Database connection
        user_id: User ID
        size_delta: Change in storage size (bytes) - can be positive or negative

    Returns:
        Updated quota record
    """
    current_time = get_current_timestamp_ms()

    query = """
        INSERT INTO user_storage_quotas (
            id, user_id, permanent_storage_used_bytes,
            permanent_storage_limit_bytes, created_at, updated_at
        ) VALUES (gen_random_uuid(), $1, GREATEST(0, $2), 524288000, $3, $3)
        ON CONFLICT (user_id) DO UPDATE SET
            permanent_storage_used_bytes = GREATEST(0, user_storage_quotas.permanent_storage_used_bytes + $2),
            updated_at = $3
        RETURNING *
    """

    return await execute_async_returning(conn, query, user_id, size_delta, current_time)


async def check_quota_limit(
    conn: asyncpg.Connection,
    user_id: str,
    additional_size: int,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Check if user has enough quota for additional storage.

    Args:
        conn: Database connection
        user_id: User ID
        additional_size: Additional storage size needed (bytes)

    Returns:
        Tuple of (has_quota, quota_record)
        has_quota: True if user has enough quota, False otherwise
        quota_record: Current quota record (None if doesn't exist)
    """
    quota = await get_user_storage_quota(conn, user_id)

    if not quota:
        # No quota record yet, create one with default limit
        quota = await create_or_update_user_storage_quota(conn, user_id, 0)

    current_usage = quota.get("permanent_storage_used_bytes", 0)
    limit = quota.get("permanent_storage_limit_bytes", 524288000)

    has_quota = (current_usage + additional_size) <= limit

    return has_quota, quota
