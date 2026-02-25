"""
User Media Service.

Handles media description updates and media retrieval for users.
"""

from typing import Optional, Dict, Any, List
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.media_assets_queries import (
    get_media_asset_by_id,
    update_media_description,
)
from src.utils.logger import get_logger

logger = get_logger()


class UserMediaService:
    """Service for managing user media assets."""

    async def update_media_description(
        self,
        user_id: str,
        media_id: str,
        description: Optional[str],
    ) -> Dict[str, Any]:
        """
        Update description for a media asset.
        When user updates description, description_model is set to NULL.

        Args:
            user_id: User ID (for ownership validation)
            media_id: Media asset UUID
            description: New description (can be None to clear)

        Returns:
            Updated media asset record

        Raises:
            ValueError: If media not found or not owned by user
        """
        try:
            async with async_db_transaction() as conn:
                # Validate ownership first
                existing = await get_media_asset_by_id(conn, media_id, user_id)
                if not existing:
                    raise ValueError(
                        f"Media {media_id} not found or does not belong to user {user_id}"
                    )

                # Update description
                updated = await update_media_description(
                    conn, media_id, description, user_id
                )

                if not updated:
                    raise ValueError(f"Failed to update media {media_id}")

                logger.info(
                    f"Updated description for media {media_id} by user {user_id}"
                )
                return updated

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error updating media description for {media_id}: {str(e)}")
            raise

    async def get_user_media(
        self,
        user_id: str,
        media_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a single media asset by ID with ownership validation.

        Args:
            user_id: User ID (for ownership validation)
            media_id: Media asset UUID

        Returns:
            Media asset record if found and owned by user, None otherwise
        """
        try:
            async with async_db_transaction() as conn:
                media = await get_media_asset_by_id(conn, media_id, user_id)
                return media

        except Exception as e:
            logger.error(f"Error getting media {media_id}: {str(e)}")
            raise

    async def list_user_media(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
        purpose: Optional[str] = None,
        dangling: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """
        List user's media assets with pagination.
        Default filter: retention_policy = 'permanent'
        Excludes automatically fetched avatars (fan_page avatar, page_scope_user profile_pic)
        as these are system-managed and shouldn't be managed/deleted by users.

        Note: suggest_response_prompt_media table was deprecated; media is now linked
        via memory_block_media. 'prompts' is always [] for backward compatibility.
        'dangling' filter uses memory_block_media (orphaned = not attached to any block).

        Args:
            user_id: User ID
            limit: Maximum number of records to return
            offset: Number of records to skip
            purpose: Optional filter by purpose (deprecated, kept for backward compatibility)
            dangling: Optional filter for orphaned media
                - None (default): Include both orphaned and non-orphaned media
                - True: Only show orphaned media (not attached to any memory block)
                - False: Only show media attached to at least one memory block

        Returns:
            List of media asset records with prompts (always [] for compatibility)
        """
        try:
            async with async_db_transaction() as conn:
                # Query media_assets only (suggest_response_prompt_media was deprecated;
                # media is now linked via memory_block_media)
                query = """
                    SELECT ma.*, '[]'::json as prompts
                    FROM media_assets ma
                    WHERE ma.user_id = $1
                      AND ma.retention_policy = 'permanent'
                      AND NOT (
                          (ma.fb_owner_type = 'fan_page' AND ma.fb_field_name = 'avatar')
                          OR (ma.fb_owner_type = 'page_scope_user' AND ma.fb_field_name = 'profile_pic')
                      )
                """

                params = [user_id]

                if dangling is True:
                    query += " AND NOT EXISTS (SELECT 1 FROM memory_block_media mbm WHERE mbm.media_id = ma.id)"
                elif dangling is False:
                    query += " AND EXISTS (SELECT 1 FROM memory_block_media mbm WHERE mbm.media_id = ma.id)"

                query += """
                    ORDER BY ma.created_at DESC
                    LIMIT $2 OFFSET $3
                """

                params.extend([limit, offset])

                from src.database.postgres.executor import execute_async_query

                results = await execute_async_query(conn, query, *params)

                # Parse prompts JSON for each result
                for result in results:
                    if isinstance(result.get("prompts"), str):
                        import json

                        try:
                            result["prompts"] = json.loads(result["prompts"])
                        except (json.JSONDecodeError, TypeError):
                            result["prompts"] = []
                    elif result.get("prompts") is None:
                        result["prompts"] = []

                return results

        except Exception as e:
            logger.error(f"Error listing media for user {user_id}: {str(e)}")
            raise

    async def delete_user_media(
        self,
        user_id: str,
        media_ids: List[str],
    ) -> Dict[str, Any]:
        """
        Delete user's media assets by IDs.
        Only deletes media owned by the user.

        Args:
            user_id: User ID (for ownership validation)
            media_ids: List of media asset UUIDs to delete

        Returns:
            Dict with deleted_count and details
        """
        try:
            from src.database.postgres.repositories.media_assets_queries import (
                get_media_assets_by_ids,
                delete_media_assets_by_ids,
                get_media_assets_for_quota_update,
            )
            from src.database.postgres.repositories.user_storage_quotas_queries import (
                create_or_update_user_storage_quota,
            )
            from src.common.s3_client import get_s3_uploader

            async with async_db_transaction() as conn:
                # Validate ownership
                media_records = await get_media_assets_by_ids(conn, media_ids, user_id)
                if len(media_records) != len(media_ids):
                    raise ValueError(
                        f"Some media IDs not found or don't belong to user {user_id}"
                    )

                # Get media for quota calculation
                media_for_quota = await get_media_assets_for_quota_update(
                    conn, media_ids
                )

                # Calculate total size to decrease from quota (only permanent media)
                permanent_media = [
                    m
                    for m in media_for_quota
                    if m.get("retention_policy") == "permanent"
                ]
                total_size_to_decrease = sum(
                    m.get("file_size_bytes", 0) for m in permanent_media
                )

                # Decrease quota for permanent media (before deletion)
                if total_size_to_decrease > 0:
                    await create_or_update_user_storage_quota(
                        conn, user_id, -total_size_to_decrease
                    )
                    logger.info(
                        f"Decreased quota by {total_size_to_decrease} bytes for user {user_id}"
                    )

                # Get S3 URLs for deletion
                s3_urls = [m["s3_url"] for m in media_for_quota if m.get("s3_url")]

                # Delete from S3 (batch operation)
                s3_client = get_s3_uploader()
                if s3_urls:
                    await s3_client.batch_delete_images_from_urls(s3_urls)
                    logger.info(f"Deleted {len(s3_urls)} media files from S3")

                # Hard delete from media_assets table
                deleted_count = await delete_media_assets_by_ids(conn, media_ids)
                logger.info(
                    f"Hard deleted {deleted_count} media assets from DB for user {user_id}"
                )

                return {
                    "deleted_count": deleted_count,
                    "media_ids": media_ids,
                    "quota_decreased_bytes": total_size_to_decrease,
                }

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error deleting media for user {user_id}: {str(e)}")
            raise

    async def persist_media_for_prompt(
        self,
        user_id: str,
        media_id: str,
    ) -> Dict[str, Any]:
        """
        Copy ephemeral media to permanent storage for prompt usage.

        Flow:
        1. Query original media_assets record by media_id
        2. Check retention_policy:
           - If already 'permanent': return existing record (no copy needed)
           - If ephemeral (one_day, one_week, etc): proceed to copy
        3. Copy S3 file to permanent/ prefix (new S3 URL)
        4. Create NEW media_assets record with:
           ✓ source_type: copy from original
           ✓ media_type: copy from original
           ✓ s3_key, s3_url: new permanent URLs
           ✓ file_size_bytes: copy from original
           ✓ mime_type, original_filename: copy from original
           ✓ retention_policy: 'permanent'
           ✓ expires_at: NULL (permanent không expire)
           ✓ status: 'ready'
           ✓ **description**: COPY from original (CRITICAL - LLM cần description trong system prompt)
           ✓ **description_model**: COPY from original
           ✓ **description_generated_at**: COPY from original
           ✓ metadata: copy from original if exists
        5. Update quota for permanent storage (+file_size_bytes)
        6. Return new permanent media record with new media_id

        IMPORTANT: Description fields phải được copy vì:
        - Agent prompt media được include trong system prompt
        - LLM không thể "nhìn" S3 URL để hiểu ảnh
        - Description là fallback khi ảnh expire hoặc để LLM hiểu context
        - Format trong system prompt: "Image(1): {description}"

        Args:
            user_id: User ID (for ownership validation)
            media_id: Media asset UUID to persist

        Returns:
            New permanent media record (with new ID) or existing if already permanent

        Raises:
            ValueError: If media not found or not owned by user
        """
        try:
            from src.database.postgres.repositories.media_assets_queries import (
                get_media_asset_by_id,
                create_media_asset,
            )
            from src.database.postgres.repositories.user_storage_quotas_queries import (
                check_quota_limit,
                create_or_update_user_storage_quota,
            )
            from src.common.s3_client import get_s3_uploader

            async with async_db_transaction() as conn:
                # 1. Query original media_assets record
                original_media = await get_media_asset_by_id(conn, media_id, user_id)
                if not original_media:
                    raise ValueError(
                        f"Media {media_id} not found or does not belong to user {user_id}"
                    )

                # 2. Check retention_policy
                retention_policy = original_media.get("retention_policy")
                if retention_policy == "permanent":
                    # Already permanent, return existing record
                    logger.debug(
                        f"Media {media_id} is already permanent, no copy needed"
                    )
                    return original_media

                # 3. Copy S3 file to permanent storage
                original_s3_url = original_media.get("s3_url")
                if not original_s3_url:
                    raise ValueError(f"Media {media_id} has no S3 URL")

                s3_client = get_s3_uploader()
                new_s3_url = await s3_client.copy_to_permanent(original_s3_url)

                if not new_s3_url:
                    raise ValueError(
                        f"Failed to copy media {media_id} to permanent storage"
                    )

                # Extract new S3 key from URL
                new_s3_key = s3_client._extract_s3_key_from_url(new_s3_url)
                if not new_s3_key:
                    raise ValueError(
                        f"Failed to extract S3 key from new URL: {new_s3_url}"
                    )

                # 4. Check quota before creating permanent media
                file_size = original_media.get("file_size_bytes", 0)
                has_quota, quota_record = await check_quota_limit(
                    conn, user_id, file_size
                )
                if not has_quota:
                    current_usage = quota_record.get("permanent_storage_used_bytes", 0)
                    limit = quota_record.get("permanent_storage_limit_bytes", 524288000)
                    available = limit - current_usage
                    raise ValueError(
                        f"Insufficient storage quota. Need {file_size} bytes, "
                        f"but only {available} bytes available (limit: {limit} bytes)"
                    )

                # 5. Create NEW permanent media_assets record with all copied fields
                new_media = await create_media_asset(
                    conn=conn,
                    user_id=user_id,
                    source_type=original_media.get("source_type"),  # Copy from original
                    media_type=original_media.get("media_type"),  # Copy from original
                    s3_key=new_s3_key,
                    s3_url=new_s3_url,
                    file_size_bytes=file_size,  # Copy from original
                    retention_policy="permanent",
                    expires_at=None,  # Permanent doesn't expire
                    mime_type=original_media.get("mime_type"),  # Copy from original
                    original_filename=original_media.get(
                        "original_filename"
                    ),  # Copy from original
                    status="ready",
                    metadata=original_media.get(
                        "metadata"
                    ),  # Copy from original if exists
                    description=original_media.get(
                        "description"
                    ),  # CRITICAL: Copy description
                    description_model=original_media.get(
                        "description_model"
                    ),  # CRITICAL: Copy model
                )

                # Note: description_generated_at is not in create_media_asset signature,
                # but we can update it separately if needed
                if original_media.get("description_generated_at"):
                    from src.database.postgres.executor import execute_async_command

                    update_query = """
                        UPDATE media_assets
                        SET description_generated_at = $1
                        WHERE id = $2
                    """
                    await execute_async_command(
                        conn,
                        update_query,
                        original_media.get("description_generated_at"),
                        new_media["id"],
                    )
                    # Update local dict
                    new_media["description_generated_at"] = original_media.get(
                        "description_generated_at"
                    )

                # 6. Update quota for permanent storage
                await create_or_update_user_storage_quota(conn, user_id, file_size)
                logger.info(
                    f"Created permanent media_asset {new_media['id']} from ephemeral {media_id}, "
                    f"increased quota by {file_size} bytes"
                )

                return new_media

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error persisting media {media_id} for prompt: {str(e)}")
            raise
