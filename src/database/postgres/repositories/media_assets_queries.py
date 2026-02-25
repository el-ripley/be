"""
Media Assets SQL query functions.
Handles CRUD operations for media_assets table (user uploads and Facebook mirrors).
"""

from typing import Optional, Dict, Any, List, Tuple
import asyncpg
from ..executor import (
    execute_async_returning,
    execute_async_query,
    execute_async_single,
)
from ..utils import generate_uuid, get_current_timestamp_ms


# ================================================================
# MEDIA ASSETS OPERATIONS
# ================================================================


async def create_media_asset(
    conn: asyncpg.Connection,
    user_id: str,
    source_type: str,
    media_type: str,
    s3_key: str,
    s3_url: str,
    file_size_bytes: int,
    retention_policy: str,
    expires_at: Optional[int],
    mime_type: Optional[str] = None,
    original_filename: Optional[str] = None,
    status: str = "ready",
    metadata: Optional[Dict[str, Any]] = None,
    description: Optional[str] = None,
    description_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new media asset record.

    Args:
        conn: Database connection
        user_id: Owner user ID
        source_type: 'user_upload' or 'facebook_mirror'
        media_type: 'image', 'video', or 'audio'
        s3_key: S3 object key
        s3_url: Full S3 URL
        file_size_bytes: File size in bytes
        retention_policy: 'one_day', 'one_week', 'two_weeks', 'one_month', or 'permanent'
        expires_at: Expiration timestamp in milliseconds (None for permanent)
        mime_type: MIME type (e.g., 'image/jpeg')
        original_filename: Original filename for user uploads
        status: 'pending', 'ready', or 'failed'
        metadata: Additional metadata JSONB

    Returns:
        Created media asset record
    """
    asset_id = generate_uuid()
    current_time = get_current_timestamp_ms()

    query = """
        INSERT INTO media_assets (
            id, user_id, source_type, media_type, s3_key, s3_url,
            file_size_bytes, retention_policy, expires_at, mime_type,
            original_filename, status, metadata, description, description_model,
            created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
        RETURNING *
    """

    return await execute_async_returning(
        conn,
        query,
        asset_id,
        user_id,
        source_type,
        media_type,
        s3_key,
        s3_url,
        file_size_bytes,
        retention_policy,
        expires_at,
        mime_type,
        original_filename,
        status,
        metadata,
        description,
        description_model,
        current_time,
        current_time,
    )


async def batch_get_media_assets_by_urls(
    conn: asyncpg.Connection,
    s3_urls: List[str],
) -> Dict[str, Dict[str, Any]]:
    """
    Batch query media_assets by s3_url array.

    Args:
        conn: Database connection
        s3_urls: List of S3 URLs to query

    Returns:
        Dict mapping s3_url -> {expires_at, retention_policy, status}
        Only includes URLs found in media_assets table
    """
    if not s3_urls:
        return {}

    query = """
        SELECT id, s3_url, expires_at, retention_policy, status, file_size_bytes
        FROM media_assets
        WHERE s3_url = ANY($1)
    """

    results = await execute_async_query(conn, query, s3_urls)

    # Build dict mapping URL to media info
    url_map: Dict[str, Dict[str, Any]] = {}
    for row in results:
        url = row.get("s3_url")
        if url:
            row_id = row.get("id")
            # Convert UUID to string if needed
            if row_id is not None:
                row_id = str(row_id)
            url_map[url] = {
                "id": row_id,
                "expires_at": row.get("expires_at"),
                "retention_policy": row.get("retention_policy"),
                "status": row.get("status"),
                "file_size_bytes": row.get("file_size_bytes"),
            }

    return url_map


async def get_media_asset_by_id(
    conn: asyncpg.Connection,
    media_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Get a single media asset by ID with ownership validation.

    Args:
        conn: Database connection
        media_id: Media asset UUID
        user_id: User ID for ownership validation

    Returns:
        Media asset record if found and owned by user, None otherwise
    """
    query = """
        SELECT *
        FROM media_assets
        WHERE id = $1 AND user_id = $2
    """
    return await execute_async_single(conn, query, media_id, user_id)


async def get_media_assets_by_ids(
    conn: asyncpg.Connection,
    media_ids: List[str],
    user_id: str,
) -> List[Dict[str, Any]]:
    """
    Get multiple media assets by IDs with ownership validation.

    Args:
        conn: Database connection
        media_ids: List of media asset UUIDs
        user_id: User ID for ownership validation

    Returns:
        List of media asset records that belong to the user
    """
    if not media_ids:
        return []

    query = """
        SELECT *
        FROM media_assets
        WHERE id = ANY($1) AND user_id = $2
        ORDER BY created_at DESC
    """
    return await execute_async_query(conn, query, media_ids, user_id)


async def update_media_description(
    conn: asyncpg.Connection,
    media_id: str,
    description: Optional[str],
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Update description for a media asset.
    When user updates description, set description_model = NULL.

    Args:
        conn: Database connection
        media_id: Media asset UUID
        description: New description (can be None to clear)
        user_id: User ID for ownership validation

    Returns:
        Updated media asset record, or None if not found or not owned by user
    """
    current_time = get_current_timestamp_ms()

    query = """
        UPDATE media_assets
        SET description = $1,
            description_model = NULL,
            updated_at = $2
        WHERE id = $3 AND user_id = $4
        RETURNING *
    """
    return await execute_async_returning(
        conn, query, description, current_time, media_id, user_id
    )


async def update_media_description_by_ai(
    conn: asyncpg.Connection,
    media_id: str,
    description: str,
    description_model: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Update description for a media asset generated by AI.
    Saves model info and generation timestamp.

    Args:
        conn: Database connection
        media_id: Media asset UUID
        description: AI-generated description
        description_model: Model used (e.g., "gpt-5-nano")
        user_id: User ID for ownership validation

    Returns:
        Updated media asset record, or None if not found or not owned by user
    """
    current_time = get_current_timestamp_ms()

    query = """
        UPDATE media_assets
        SET description = $1,
            description_model = $2,
            description_generated_at = $3,
            updated_at = $4
        WHERE id = $5 AND user_id = $6
        RETURNING *
    """
    return await execute_async_returning(
        conn,
        query,
        description,
        description_model,
        current_time,
        current_time,
        media_id,
        user_id,
    )


async def update_media_retention_policy(
    conn: asyncpg.Connection,
    media_id: str,
    retention_policy: str,
    expires_at: Optional[int],
) -> Optional[Dict[str, Any]]:
    """
    Update retention policy of a media asset (e.g., from ephemeral to permanent).

    Args:
        conn: Database connection
        media_id: Media asset UUID
        retention_policy: New retention policy
        expires_at: New expiration timestamp (None for permanent)

    Returns:
        Updated media asset record, or None if not found
    """
    current_time = get_current_timestamp_ms()

    query = """
        UPDATE media_assets
        SET retention_policy = $2,
            expires_at = $3,
            updated_at = $4
        WHERE id = $1
        RETURNING *
    """
    return await execute_async_returning(
        conn, query, media_id, retention_policy, expires_at, current_time
    )


async def update_media_retention_and_location(
    conn: asyncpg.Connection,
    media_id: str,
    s3_key: str,
    s3_url: str,
    retention_policy: str,
    expires_at: Optional[int],
) -> Optional[Dict[str, Any]]:
    """
    Update media asset retention policy AND S3 location atomically.
    Used by change_media_retention tool after S3 file is copied to new prefix.

    Args:
        conn: Database connection
        media_id: Media asset UUID
        s3_key: New S3 key (with new retention prefix)
        s3_url: New S3 URL
        retention_policy: New retention policy
        expires_at: New expiration timestamp (None for permanent)

    Returns:
        Updated media asset record, or None if not found
    """
    current_time = get_current_timestamp_ms()

    query = """
        UPDATE media_assets
        SET s3_key = $2,
            s3_url = $3,
            retention_policy = $4,
            expires_at = $5,
            updated_at = $6
        WHERE id = $1
        RETURNING *
    """
    return await execute_async_returning(
        conn,
        query,
        media_id,
        s3_key,
        s3_url,
        retention_policy,
        expires_at,
        current_time,
    )


async def delete_media_assets_by_ids(
    conn: asyncpg.Connection,
    media_ids: List[str],
) -> int:
    """
    Hard delete media assets by IDs.
    No soft delete - removes records completely from DB.

    Args:
        conn: Database connection
        media_ids: List of media asset UUIDs to delete

    Returns:
        Number of records deleted
    """
    if not media_ids:
        return 0

    query = "DELETE FROM media_assets WHERE id = ANY($1)"
    result = await conn.execute(query, media_ids)
    # result is a string like "DELETE 5", extract the number
    deleted_count = int(result.split()[-1]) if result.split()[-1].isdigit() else 0
    return deleted_count


async def get_media_assets_for_quota_update(
    conn: asyncpg.Connection,
    media_ids: List[str],
) -> List[Dict[str, Any]]:
    """
    Get media assets with file_size_bytes for quota calculation.

    Args:
        conn: Database connection
        media_ids: List of media asset UUIDs

    Returns:
        List of media records with id, user_id, file_size_bytes, retention_policy
    """
    if not media_ids:
        return []

    query = """
        SELECT id, user_id, file_size_bytes, retention_policy, s3_key, s3_url
        FROM media_assets
        WHERE id = ANY($1)
    """
    return await execute_async_query(conn, query, media_ids)


# ================================================================
# FACEBOOK MIRROR SPECIFIC OPERATIONS
# ================================================================


async def get_fb_media_asset(
    conn: asyncpg.Connection,
    fb_owner_type: str,
    fb_owner_id: str,
    fb_field_name: str,
) -> Optional[Dict[str, Any]]:
    """
    Get Facebook media asset by owner info.

    Args:
        conn: Database connection
        fb_owner_type: Facebook owner type ('fan_page', 'page_scope_user', 'post', 'comment', 'message')
        fb_owner_id: Facebook owner ID
        fb_field_name: Field name (e.g., 'avatar', 'photo_url', 'profile_pic')

    Returns:
        Media asset record if found, None otherwise
    """
    query = """
        SELECT *
        FROM media_assets
        WHERE fb_owner_type = $1
          AND fb_owner_id = $2
          AND fb_field_name = $3
          AND source_type = 'facebook_mirror'
        LIMIT 1
    """
    return await execute_async_single(
        conn, query, fb_owner_type, fb_owner_id, fb_field_name
    )


async def get_fb_media_assets_batch(
    conn: asyncpg.Connection,
    items: List[Tuple[str, str, str]],  # [(owner_type, owner_id, field_name), ...]
) -> Dict[str, Dict[str, Any]]:
    """
    Batch fetch Facebook media assets by owner info.

    Args:
        conn: Database connection
        items: List of tuples (fb_owner_type, fb_owner_id, fb_field_name)

    Returns:
        Dict mapping f"{owner_type}:{owner_id}:{field_name}" -> media_asset
        Only includes items that exist in database
    """
    if not items:
        return {}

    # Build query with multiple OR conditions
    # Using ANY with array of composite types would be cleaner but asyncpg doesn't support it well
    # So we use a simpler approach with multiple OR conditions
    conditions = []
    params = []
    param_idx = 1

    for owner_type, owner_id, field_name in items:
        conditions.append(
            f"(fb_owner_type = ${param_idx} AND fb_owner_id = ${param_idx + 1} AND fb_field_name = ${param_idx + 2})"
        )
        params.extend([owner_type, owner_id, field_name])
        param_idx += 3

    query = f"""
        SELECT *
        FROM media_assets
        WHERE source_type = 'facebook_mirror'
          AND ({' OR '.join(conditions)})
    """

    results = await execute_async_query(conn, query, *params)

    # Build dict with composite key
    assets_map: Dict[str, Dict[str, Any]] = {}
    for row in results:
        owner_type = row.get("fb_owner_type")
        owner_id = row.get("fb_owner_id")
        field_name = row.get("fb_field_name")
        if owner_type and owner_id and field_name:
            key = f"{owner_type}:{owner_id}:{field_name}"
            assets_map[key] = row

    return assets_map


async def upsert_fb_media_asset(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    fb_owner_type: str,
    fb_owner_id: str,
    fb_field_name: str,
    source_url: Optional[str],
    source_hash: Optional[str],
    media_type: str,
    s3_key: str,
    s3_url: str,
    file_size_bytes: int,
    status: str,
    retention_policy: str,
    expires_at: Optional[int],
    metadata: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    description: Optional[str] = None,
    description_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upsert Facebook media asset into media_assets table.
    Uses ON CONFLICT on (fb_owner_type, fb_owner_id, fb_field_name) for deduplication.

    Args:
        conn: Database connection
        user_id: User ID who owns this media (the user whose agent fetched it)
        fb_owner_type: Facebook owner type
        fb_owner_id: Facebook owner ID
        fb_field_name: Field name
        source_url: Original Facebook URL
        source_hash: SHA256/ETag for deduplication
        media_type: 'image', 'video', or 'audio'
        s3_key: S3 object key
        s3_url: Full S3 URL
        file_size_bytes: File size in bytes
        status: 'pending', 'ready', or 'failed'
        retention_policy: Retention policy
        expires_at: Expiration timestamp in milliseconds (None for permanent)
        metadata: Additional metadata JSONB
        error_message: Error message if status is 'failed'
        description: AI-generated description
        description_model: Model used to generate description

    Returns:
        Upserted media asset record
    """
    current_time = get_current_timestamp_ms()

    # Check if record exists
    existing = await get_fb_media_asset(conn, fb_owner_type, fb_owner_id, fb_field_name)

    if existing:
        # Update existing record
        # Preserve description if new one is None
        final_description = (
            description if description is not None else existing.get("description")
        )
        final_description_model = (
            description_model
            if description_model is not None
            else existing.get("description_model")
        )

        update_query = """
            UPDATE media_assets
            SET user_id = $1,
                source_url = $2,
                source_hash = $3,
                media_type = $4,
                s3_key = $5,
                s3_url = $6,
                file_size_bytes = $7,
                status = $8,
                retention_policy = $9,
                expires_at = $10,
                metadata = $11,
                error_message = $12,
                description = $13,
                description_model = $14,
                last_checked_at = $15,
                updated_at = $15
            WHERE id = $16
            RETURNING *
        """
        return await execute_async_returning(
            conn,
            update_query,
            user_id,
            source_url,
            source_hash,
            media_type,
            s3_key,
            s3_url,
            file_size_bytes,
            status,
            retention_policy,
            expires_at,
            metadata,
            error_message,
            final_description,
            final_description_model,
            current_time,
            existing.get("id"),
        )
    else:
        # Insert new record
        asset_id = generate_uuid()
        insert_query = """
            INSERT INTO media_assets (
                id, user_id, source_type, fb_owner_type, fb_owner_id, fb_field_name,
                source_url, source_hash, media_type, s3_key, s3_url,
                file_size_bytes, status, retention_policy, expires_at,
                metadata, error_message, description, description_model,
                last_checked_at, created_at, updated_at
            )
            VALUES (
                $1, $2, 'facebook_mirror', $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17, $18,
                $19, $19, $19
            )
            RETURNING *
        """
        return await execute_async_returning(
            conn,
            insert_query,
            asset_id,
            user_id,
            fb_owner_type,
            fb_owner_id,
            fb_field_name,
            source_url,
            source_hash,
            media_type,
            s3_key,
            s3_url,
            file_size_bytes,
            status,
            retention_policy,
            expires_at,
            metadata,
            error_message,
            description,
            description_model,
            current_time,
        )


async def update_media_description_by_id(
    conn: asyncpg.Connection,
    media_id: str,
    description: Optional[str],
    description_model: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Update description and description_model for a media asset.
    Used after vision model processing.

    Args:
        conn: Database connection
        media_id: Media asset UUID
        description: Generated description text
        description_model: Model used to generate (e.g., 'gpt-5-nano')

    Returns:
        Updated media asset record, or None if not found
    """
    current_time = get_current_timestamp_ms()

    query = """
        UPDATE media_assets
        SET description = $1,
            description_model = $2,
            description_generated_at = $3,
            updated_at = $3
        WHERE id = $4
        RETURNING *
    """
    return await execute_async_returning(
        conn, query, description, description_model, current_time, media_id
    )
