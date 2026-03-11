"""
User files queries for PostgreSQL database operations.

This module contains all database operations related to user file storage,
including file tracking and storage quota management.
"""

import time
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from src.database.postgres.connection import get_async_connection
from src.utils.logger import get_logger

logger = get_logger()


async def create_user_file(
    user_id: str,
    filename: str,
    display_name: str,
    file_type: str,
    file_extension: str,
    mime_type: str,
    file_size: int,
    s3_key: str,
    s3_url: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Create a new user file record.

    Args:
        user_id: ID of the user who owns the file
        filename: Original filename from user
        display_name: User-editable display name
        file_type: 'image' or 'video'
        file_extension: File extension (.jpg, .mp4, etc.)
        mime_type: MIME type (image/jpeg, video/mp4, etc.)
        file_size: Size in bytes
        s3_key: S3 object key
        s3_url: Full S3 URL
        metadata: Optional metadata dictionary

    Returns:
        The ID of the created file record
    """
    file_id = str(uuid4())
    current_time = int(time.time())

    query = """
        INSERT INTO user_files (
            id, user_id, filename, display_name, file_type, file_extension,
            mime_type, file_size, s3_key, s3_url, metadata, created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        RETURNING id
    """

    async with get_async_connection() as conn:
        result = await conn.fetchrow(
            query,
            file_id,
            user_id,
            filename,
            display_name,
            file_type,
            file_extension,
            mime_type,
            file_size,
            s3_key,
            s3_url,
            metadata,
            current_time,
            current_time,
        )

        logger.info(f"Created user file record: {file_id} for user: {user_id}")
        return result["id"]


async def get_user_files(
    user_id: str,
    file_type: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Get user files with optional filtering.

    Args:
        user_id: ID of the user
        file_type: Optional filter by 'image' or 'video'
        limit: Optional limit for pagination
        offset: Optional offset for pagination

    Returns:
        List of user file records
    """
    base_query = """
        SELECT id, user_id, filename, display_name, file_type, file_extension,
               mime_type, file_size, s3_key, s3_url, metadata, created_at, updated_at
        FROM user_files
        WHERE user_id = $1
    """

    params = [user_id]
    param_count = 1

    if file_type:
        param_count += 1
        base_query += f" AND file_type = ${param_count}"
        params.append(file_type)

    base_query += " ORDER BY created_at DESC"

    if limit:
        param_count += 1
        base_query += f" LIMIT ${param_count}"
        params.append(limit)

    if offset:
        param_count += 1
        base_query += f" OFFSET ${param_count}"
        params.append(offset)

    async with get_async_connection() as conn:
        results = await conn.fetch(base_query, *params)
        return [dict(row) for row in results]


async def get_user_file_by_id(user_id: str, file_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a specific user file by ID.

    Args:
        user_id: ID of the user (for security)
        file_id: ID of the file

    Returns:
        File record or None if not found
    """
    query = """
        SELECT id, user_id, filename, display_name, file_type, file_extension,
               mime_type, file_size, s3_key, s3_url, metadata, created_at, updated_at
        FROM user_files
        WHERE id = $1 AND user_id = $2
    """

    async with get_async_connection() as conn:
        result = await conn.fetchrow(query, file_id, user_id)
        return dict(result) if result else None


async def get_user_files_by_ids(
    user_id: str, file_ids: List[str]
) -> List[Dict[str, Any]]:
    """
    Get multiple user files by IDs.

    Args:
        user_id: ID of the user (for security)
        file_ids: List of file IDs

    Returns:
        List of file records
    """
    if not file_ids:
        return []

    query = """
        SELECT id, user_id, filename, display_name, file_type, file_extension,
               mime_type, file_size, s3_key, s3_url, metadata, created_at, updated_at
        FROM user_files
        WHERE id = ANY($1) AND user_id = $2
    """

    async with get_async_connection() as conn:
        results = await conn.fetch(query, file_ids, user_id)
        return [dict(row) for row in results]


async def update_user_file_metadata(
    user_id: str,
    file_id: str,
    display_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Update user file metadata.

    Args:
        user_id: ID of the user (for security)
        file_id: ID of the file to update
        display_name: New display name (optional)
        metadata: New metadata (optional)

    Returns:
        True if update was successful, False if file not found
    """
    updates = []
    params = []
    param_count = 0

    if display_name is not None:
        param_count += 1
        updates.append(f"display_name = ${param_count}")
        params.append(display_name)

    if metadata is not None:
        param_count += 1
        updates.append(f"metadata = ${param_count}")
        params.append(metadata)

    if not updates:
        return True  # No updates needed

    param_count += 1
    updates.append(f"updated_at = ${param_count}")
    params.append(int(time.time()))

    # Add WHERE clause parameters
    param_count += 1
    params.append(file_id)
    param_count += 1
    params.append(user_id)

    query = f"""
        UPDATE user_files
        SET {', '.join(updates)}
        WHERE id = ${param_count - 1} AND user_id = ${param_count}
    """

    async with get_async_connection() as conn:
        result = await conn.execute(query, *params)
        success = result.split()[-1] == "1"  # "UPDATE 1" means 1 row affected

        if success:
            logger.info(f"Updated metadata for file: {file_id}")
        else:
            logger.warning(f"File not found for update: {file_id}")

        return success


async def delete_user_files(
    user_id: str, file_ids: List[str]
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Delete multiple user files and return their S3 info for cleanup.

    Args:
        user_id: ID of the user (for security)
        file_ids: List of file IDs to delete

    Returns:
        Tuple of (deleted_files_info, count_deleted)
    """
    if not file_ids:
        return [], 0

    # First get the files to be deleted for S3 cleanup
    query_select = """
        SELECT id, s3_key, s3_url, file_size, file_type
        FROM user_files
        WHERE id = ANY($1) AND user_id = $2
    """

    query_delete = """
        DELETE FROM user_files
        WHERE id = ANY($1) AND user_id = $2
    """

    async with get_async_connection() as conn:
        # Get files info before deletion
        files_to_delete = await conn.fetch(query_select, file_ids, user_id)
        deleted_files = [dict(row) for row in files_to_delete]

        # Delete the files
        result = await conn.execute(query_delete, file_ids, user_id)
        count_deleted = int(result.split()[-1])  # Extract count from "DELETE N"

        logger.info(f"Deleted {count_deleted} files for user: {user_id}")
        return deleted_files, count_deleted


async def get_user_storage_usage(user_id: str) -> Dict[str, Any]:
    """
    Get user storage usage information.

    Args:
        user_id: ID of the user

    Returns:
        Storage usage information
    """
    query = """
        SELECT user_id, total_size, file_count, image_count, video_count, updated_at
        FROM user_storage_usage
        WHERE user_id = $1
    """

    async with get_async_connection() as conn:
        result = await conn.fetchrow(query, user_id)
        if result:
            return dict(result)
        else:
            # Return default values if no record exists
            return {
                "user_id": user_id,
                "total_size": 0,
                "file_count": 0,
                "image_count": 0,
                "video_count": 0,
                "updated_at": int(time.time()),
            }


async def update_user_storage_usage(
    user_id: str,
    size_delta: int,
    file_count_delta: int,
    image_count_delta: int = 0,
    video_count_delta: int = 0,
) -> Dict[str, Any]:
    """
    Update user storage usage (create if doesn't exist).

    Args:
        user_id: ID of the user
        size_delta: Change in total size (can be negative for deletions)
        file_count_delta: Change in file count (can be negative)
        image_count_delta: Change in image count
        video_count_delta: Change in video count

    Returns:
        Updated storage usage information
    """
    current_time = int(time.time())

    query = """
        INSERT INTO user_storage_usage (
            user_id, total_size, file_count, image_count, video_count, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (user_id) DO UPDATE SET
            total_size = user_storage_usage.total_size + $2,
            file_count = user_storage_usage.file_count + $3,
            image_count = user_storage_usage.image_count + $4,
            video_count = user_storage_usage.video_count + $5,
            updated_at = $6
        RETURNING user_id, total_size, file_count, image_count, video_count, updated_at
    """

    async with get_async_connection() as conn:
        result = await conn.fetchrow(
            query,
            user_id,
            size_delta,
            file_count_delta,
            image_count_delta,
            video_count_delta,
            current_time,
        )

        usage_info = dict(result)
        logger.info(f"Updated storage usage for user {user_id}: {usage_info}")
        return usage_info


async def get_user_file_count_and_size(user_id: str) -> Tuple[int, int]:
    """
    Get actual file count and total size from user_files table.
    Used for validation and sync purposes.

    Args:
        user_id: ID of the user

    Returns:
        Tuple of (file_count, total_size)
    """
    query = """
        SELECT COUNT(*) as file_count, COALESCE(SUM(file_size), 0) as total_size
        FROM user_files
        WHERE user_id = $1
    """

    async with get_async_connection() as conn:
        result = await conn.fetchrow(query, user_id)
        return int(result["file_count"]), int(result["total_size"])
