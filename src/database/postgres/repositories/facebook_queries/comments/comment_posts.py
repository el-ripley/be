from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from src.database.postgres.executor import (
    execute_async_query,
    execute_async_returning,
    execute_async_single,
)
from src.database.postgres.utils import get_current_timestamp


async def create_post(
    conn: asyncpg.Connection,
    post_id: str,
    fan_page_id: str,
    message: Optional[str] = None,
    video_link: Optional[str] = None,
    photo_link: Optional[str] = None,
    facebook_created_time: Optional[int] = None,
    # Engagement fields
    full_picture: Optional[str] = None,
    permalink_url: Optional[str] = None,
    status_type: Optional[str] = None,
    is_published: Optional[bool] = True,
    reaction_total_count: Optional[int] = 0,
    reaction_like_count: Optional[int] = 0,
    reaction_love_count: Optional[int] = 0,
    reaction_haha_count: Optional[int] = 0,
    reaction_wow_count: Optional[int] = 0,
    reaction_sad_count: Optional[int] = 0,
    reaction_angry_count: Optional[int] = 0,
    reaction_care_count: Optional[int] = 0,
    share_count: Optional[int] = 0,
    comment_count: Optional[int] = 0,
    reactions_fetched_at: Optional[int] = None,
    engagement_fetched_at: Optional[int] = None,
    created_at: Optional[int] = None,
    updated_at: Optional[int] = None,
) -> Dict[str, Any]:
    """Create or update a post record."""
    current_time = get_current_timestamp()

    query = """
        INSERT INTO posts (
            id, fan_page_id, message, video_link, photo_link, facebook_created_time,
            full_picture, permalink_url, status_type, is_published,
            reaction_total_count, reaction_like_count, reaction_love_count,
            reaction_haha_count, reaction_wow_count, reaction_sad_count,
            reaction_angry_count, reaction_care_count, share_count, comment_count,
            reactions_fetched_at, engagement_fetched_at,
            created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24)
        ON CONFLICT (id) DO UPDATE SET
            message = EXCLUDED.message,
            video_link = EXCLUDED.video_link,
            photo_link = EXCLUDED.photo_link,
            facebook_created_time = EXCLUDED.facebook_created_time,
            full_picture = EXCLUDED.full_picture,
            permalink_url = EXCLUDED.permalink_url,
            status_type = EXCLUDED.status_type,
            is_published = EXCLUDED.is_published,
            reaction_total_count = EXCLUDED.reaction_total_count,
            reaction_like_count = EXCLUDED.reaction_like_count,
            reaction_love_count = EXCLUDED.reaction_love_count,
            reaction_haha_count = EXCLUDED.reaction_haha_count,
            reaction_wow_count = EXCLUDED.reaction_wow_count,
            reaction_sad_count = EXCLUDED.reaction_sad_count,
            reaction_angry_count = EXCLUDED.reaction_angry_count,
            reaction_care_count = EXCLUDED.reaction_care_count,
            share_count = EXCLUDED.share_count,
            comment_count = EXCLUDED.comment_count,
            reactions_fetched_at = EXCLUDED.reactions_fetched_at,
            engagement_fetched_at = EXCLUDED.engagement_fetched_at,
            updated_at = EXCLUDED.updated_at
        RETURNING 
            *,
            (created_at = updated_at) AS is_new_post
    """

    return await execute_async_returning(
        conn,
        query,
        post_id,
        fan_page_id,
        message,
        video_link,
        photo_link,
        facebook_created_time,
        full_picture,
        permalink_url,
        status_type,
        is_published,
        reaction_total_count,
        reaction_like_count,
        reaction_love_count,
        reaction_haha_count,
        reaction_wow_count,
        reaction_sad_count,
        reaction_angry_count,
        reaction_care_count,
        share_count,
        comment_count,
        reactions_fetched_at,
        engagement_fetched_at,
        created_at or current_time,
        updated_at or current_time,
    )


async def get_post_by_id(
    conn: asyncpg.Connection, post_id: str
) -> Optional[Dict[str, Any]]:
    """Get a post by ID from the database with media asset info."""
    query = """
        SELECT 
            p.*,
            photo_asset.id AS photo_media_id,
            photo_asset.s3_url AS photo_s3_url,
            photo_asset.status AS photo_s3_status,
            photo_asset.retention_policy AS photo_retention_policy,
            photo_asset.expires_at AS photo_expires_at,
            photo_asset.description AS photo_description
        FROM posts p
        LEFT JOIN media_assets photo_asset
            ON photo_asset.source_type = 'facebook_mirror'
           AND photo_asset.fb_owner_type = 'post'
           AND photo_asset.fb_owner_id::text = p.id::text
           AND photo_asset.fb_field_name = 'photo_link'
        WHERE p.id = $1
    """
    result = await execute_async_single(conn, query, post_id)
    if not result:
        return None

    # Convert UUID to string
    photo_media_id_raw = result.get("photo_media_id")
    photo_media_id = None
    if photo_media_id_raw is not None:
        photo_media_id = (
            str(photo_media_id_raw)
            if hasattr(photo_media_id_raw, "__str__")
            else photo_media_id_raw
        )

    # Build photo_media dict
    photo_media = {
        "id": photo_media_id,
        "s3_url": result.get("photo_s3_url"),
        "status": result.get("photo_s3_status"),
        "retention_policy": result.get("photo_retention_policy"),
        "expires_at": result.get("photo_expires_at"),
        "description": result.get("photo_description"),
        "original_url": result.get("photo_link"),
    }
    result["photo_media"] = photo_media

    # Clean up extra keys
    for extra_key in (
        "photo_media_id",
        "photo_s3_url",
        "photo_s3_status",
        "photo_retention_policy",
        "photo_expires_at",
        "photo_description",
    ):
        result.pop(extra_key, None)

    return result


async def list_posts_by_page(
    conn: asyncpg.Connection,
    fan_page_id: str,
    limit: int = 20,
    cursor: Optional[Tuple[int, str]] = None,
    need_comment_sync: Optional[bool] = None,
) -> Tuple[List[Dict[str, Any]], bool, Optional[Tuple[int, str]]]:
    """
    List posts for a page with cursor-based pagination.

    Args:
        conn: Database connection
        fan_page_id: Page ID to filter posts
        limit: Max posts to return (1-100)
        cursor: Optional cursor tuple (facebook_created_time, post_id) for pagination
        need_comment_sync: Optional filter - True = only posts needing comment sync,
                          False = only posts with completed comment sync, None = all

    Returns:
        Tuple of (posts list, has_more, next_cursor)
    """
    limit = max(1, min(limit, 100))

    # Build WHERE clause
    where_conditions = ["p.fan_page_id = $1"]
    params: List[Any] = [fan_page_id]
    param_idx = 2

    # Filter by comment sync status
    if need_comment_sync is not None:
        if need_comment_sync:
            # Only posts needing comment sync (no sync state or status != 'completed')
            where_conditions.append("(pcs.status IS NULL OR pcs.status != 'completed')")
        else:
            # Only posts with completed comment sync
            where_conditions.append("pcs.status = 'completed'")

    # Cursor condition
    if cursor:
        cursor_time, cursor_id = cursor
        where_conditions.append(
            f"(p.facebook_created_time, p.id) < (${param_idx}, ${param_idx + 1})"
        )
        params.extend([cursor_time, cursor_id])
        param_idx += 2

    where_clause = " AND ".join(where_conditions)

    # Add limit + 1 to detect has_more
    params.append(limit + 1)
    limit_placeholder = f"${param_idx}"

    query = f"""
        SELECT 
            p.id,
            p.fan_page_id,
            p.message,
            p.video_link,
            p.photo_link,
            p.facebook_created_time,
            p.full_picture,
            p.permalink_url,
            p.status_type,
            p.is_published,
            p.reaction_total_count,
            p.share_count,
            p.comment_count,
            p.created_at,
            p.updated_at,
            p.facebook_created_time AS sort_time,
            -- Comment sync status
            pcs.status AS comment_sync_status,
            pcs.total_synced_root_comments,
            pcs.total_synced_comments,
            pcs.last_sync_at AS comment_last_sync_at
        FROM posts p
        LEFT JOIN facebook_post_comment_sync_states pcs ON p.id = pcs.post_id
        WHERE {where_clause}
        ORDER BY p.facebook_created_time DESC NULLS LAST, p.id DESC
        LIMIT {limit_placeholder}
    """

    rows = await execute_async_query(conn, query, *params)

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = (
            last.get("sort_time") or last.get("facebook_created_time") or 0,
            last.get("id"),
        )

    return rows, has_more, next_cursor
