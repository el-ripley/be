"""
User files service for handling file uploads to ephemeral S3 storage.

Tracks uploads in media_assets table for lifecycle management.
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from src.common.s3_client import get_s3_uploader
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.media_assets_queries import create_media_asset
from src.utils.logger import get_logger

logger = get_logger()


class UserFilesService:
    """Service for managing user file storage operations with ephemeral storage."""

    # Individual file size limits (no total quota)
    MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
    MAX_VIDEO_SIZE = 25 * 1024 * 1024  # 25MB
    MAX_FILES_PER_BATCH = 10

    # Supported file types
    SUPPORTED_IMAGE_TYPES = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }

    SUPPORTED_VIDEO_TYPES = {
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/x-msvideo": ".avi",
        "video/webm": ".webm",
    }

    def __init__(self):
        self.s3_uploader = get_s3_uploader()

    async def upload_files(
        self,
        user_id: str,
        files: List[Dict[str, Any]],
        purpose: str,
        descriptions: Optional[List[Optional[str]]] = None,
    ) -> Dict[str, Any]:
        """
        Upload multiple files to ephemeral S3 storage.

        Args:
            user_id: ID of the user
            files: List of file dictionaries with 'filename', 'content', 'content_type' keys
            purpose: Upload purpose - 'facebook' (1 day) or 'agent' (7 days)
            descriptions: Optional list of descriptions matching files order (can be None or shorter than files)

        Returns:
            Dictionary with upload results and statistics
        """
        if not files:
            return {
                "success": True,
                "results": [],
                "successful_uploads": 0,
                "total_files": 0,
                "message": "No files provided",
            }

        if len(files) > self.MAX_FILES_PER_BATCH:
            return {
                "success": False,
                "error": f"Maximum {self.MAX_FILES_PER_BATCH} files allowed per batch",
                "results": [],
                "successful_uploads": 0,
                "total_files": len(files),
            }

        logger.info(f"Starting batch upload of {len(files)} files for user {user_id}")

        # Validate all files first
        validation_results = []
        for i, file_data in enumerate(files):
            validation = await self._validate_file(file_data)
            validation_results.append(validation)

        # Process uploads
        results = []
        successful_uploads = 0

        # Normalize descriptions list (ensure it matches files length)
        if descriptions is None:
            descriptions = [None] * len(files)
        elif len(descriptions) < len(files):
            # Pad with None if descriptions list is shorter
            descriptions.extend([None] * (len(files) - len(descriptions)))

        for i, (file_data, validation) in enumerate(zip(files, validation_results)):
            if not validation["valid"]:
                results.append(
                    {
                        "success": False,
                        "filename": file_data.get("filename", f"file_{i+1}"),
                        "error": validation["error"],
                        "url": None,
                        "file_id": None,
                        "description": None,
                    }
                )
                continue

            try:
                # Upload to S3 and track in media_assets table
                description = descriptions[i] if i < len(descriptions) else None
                upload_result = await self._upload_single_file(
                    user_id, file_data, validation, purpose, description
                )
                results.append(upload_result)

                if upload_result["success"]:
                    successful_uploads += 1

            except Exception as e:
                logger.error(f"Error uploading file {i+1}: {e}")
                results.append(
                    {
                        "success": False,
                        "filename": file_data.get("filename", f"file_{i+1}"),
                        "error": f"Upload failed: {str(e)}",
                        "url": None,
                        "file_id": None,
                    }
                )

        logger.info(
            f"Upload completed: {successful_uploads}/{len(files)} files uploaded successfully"
        )

        return {
            "success": successful_uploads > 0,
            "results": results,
            "successful_uploads": successful_uploads,
            "total_files": len(files),
            "message": f"Successfully uploaded {successful_uploads} out of {len(files)} files",
        }

    async def _validate_file(self, file_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate a single file from FormData before upload.

        Args:
            file_data: File data dictionary with 'filename', 'content', 'content_type'

        Returns:
            Validation result dictionary
        """
        try:
            filename = file_data.get("filename", "unknown")
            content = file_data.get("content", b"")
            content_type = file_data.get("content_type", "application/octet-stream")

            if not content:
                return {
                    "valid": False,
                    "error": "File is empty",
                    "filename": filename,
                }

            # Determine file type and validate
            file_type = None
            file_extension = None
            max_size = 0

            if content_type in self.SUPPORTED_IMAGE_TYPES:
                file_type = "image"
                file_extension = self.SUPPORTED_IMAGE_TYPES[content_type]
                max_size = self.MAX_IMAGE_SIZE
            elif content_type in self.SUPPORTED_VIDEO_TYPES:
                file_type = "video"
                file_extension = self.SUPPORTED_VIDEO_TYPES[content_type]
                max_size = self.MAX_VIDEO_SIZE
            else:
                supported_types = list(self.SUPPORTED_IMAGE_TYPES.keys()) + list(
                    self.SUPPORTED_VIDEO_TYPES.keys()
                )
                return {
                    "valid": False,
                    "error": f"Unsupported file type: {content_type}. Supported: {', '.join(supported_types)}",
                    "filename": filename,
                }

            if len(content) > max_size:
                return {
                    "valid": False,
                    "error": f"File size ({len(content)} bytes) exceeds maximum for {file_type} ({max_size} bytes)",
                    "filename": filename,
                }

            return {
                "valid": True,
                "filename": filename,
                "file_type": file_type,
                "file_extension": file_extension,
                "mime_type": content_type,
                "size": len(content),
                "data": content,
            }

        except Exception as e:
            logger.error(f"Error validating file: {e}")
            return {
                "valid": False,
                "error": f"Validation failed: {str(e)}",
                "filename": file_data.get("filename", "unknown"),
            }

    async def _upload_single_file(
        self,
        user_id: str,
        file_data: Dict[str, Any],
        validation: Dict[str, Any],
        purpose: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Upload a single validated file to ephemeral S3 storage and track in media_assets.

        Args:
            user_id: ID of the user
            file_data: Original file data
            validation: Validation result
            purpose: Upload purpose - 'facebook' (1 day) or 'agent' (7 days)
            description: Optional description for the media

        Returns:
            Upload result dictionary with file_id and description
        """
        try:
            # Map purpose to retention policy and S3 prefix
            if purpose == "prompt":
                # Upload to permanent storage for prompt media (counts toward quota)
                retention_policy = "permanent"
                prefix = "permanent"
                cache_control = "max-age=31536000"  # Cache for 1 year
                expires_at = None  # No expiration for permanent
                days = None
            elif purpose == "agent":
                retention_policy = "one_week"
                prefix = "ephemeral/one_week"
                cache_control = "max-age=604800"  # Cache for 7 days
                days = 7
            else:  # purpose == "facebook"
                retention_policy = "one_day"
                prefix = "ephemeral/one_day"
                cache_control = "max-age=86400"  # Cache for 1 day
                days = 1

            # Generate unique S3 key
            unique_id = str(uuid.uuid4())
            s3_key = f"{prefix}/{unique_id}{validation['file_extension']}"

            # Upload to S3 (offload to thread to avoid blocking the event loop)
            await asyncio.to_thread(
                self.s3_uploader.s3_client.put_object,
                Bucket=self.s3_uploader.bucket_name,
                Key=s3_key,
                Body=validation["data"],
                ContentType=validation["mime_type"],
                CacheControl=cache_control,
            )

            # Generate public S3 URL
            from src.database.postgres.utils import get_current_timestamp_ms
            from src.settings import settings

            s3_url = f"https://{self.s3_uploader.bucket_name}.s3.{settings.aws_region}.amazonaws.com/{s3_key}"

            # Calculate expires_at (milliseconds) - only for ephemeral
            current_time_ms = get_current_timestamp_ms()
            if days is not None:
                expires_at = current_time_ms + (days * 24 * 60 * 60 * 1000)
            else:
                expires_at = None  # Permanent has no expiration

            # Insert into media_assets table
            file_id = None
            try:
                async with async_db_transaction() as conn:
                    # For permanent media (purpose='prompt'), check quota first
                    if retention_policy == "permanent":
                        from src.database.postgres.repositories.user_storage_quotas_queries import (
                            check_quota_limit,
                            create_or_update_user_storage_quota,
                        )

                        # Check quota before creating permanent media
                        has_quota, quota_record = await check_quota_limit(
                            conn, user_id, validation["size"]
                        )
                        if not has_quota:
                            current_usage = quota_record.get(
                                "permanent_storage_used_bytes", 0
                            )
                            limit = quota_record.get(
                                "permanent_storage_limit_bytes", 524288000
                            )
                            available = limit - current_usage
                            raise ValueError(
                                f"Insufficient storage quota. Need {validation['size']} bytes, "
                                f"but only {available} bytes available (limit: {limit} bytes)"
                            )

                    media_asset = await create_media_asset(
                        conn=conn,
                        user_id=user_id,
                        source_type="user_upload",
                        media_type=validation["file_type"],
                        s3_key=s3_key,
                        s3_url=s3_url,
                        file_size_bytes=validation["size"],
                        retention_policy=retention_policy,
                        expires_at=expires_at,
                        mime_type=validation["mime_type"],
                        original_filename=validation["filename"],
                        status="ready",
                        metadata=None,
                        description=description,
                        description_model=None,  # User-provided, not AI
                    )
                    file_id = str(media_asset["id"])

                    # Update quota for permanent media
                    if retention_policy == "permanent":
                        from src.database.postgres.repositories.user_storage_quotas_queries import (
                            create_or_update_user_storage_quota,
                        )

                        await create_or_update_user_storage_quota(
                            conn, user_id, validation["size"]
                        )
                        logger.debug(
                            f"Created permanent media_asset {file_id}, increased quota by {validation['size']} bytes"
                        )
                    else:
                        logger.debug(
                            f"Created ephemeral media_asset record: {file_id} for {s3_url}"
                        )
            except Exception as db_error:
                # Log error but don't fail upload if S3 succeeded
                logger.error(
                    f"Failed to create media_asset record for {s3_url}: {db_error}. "
                    f"S3 upload succeeded, continuing without DB record."
                )

            return {
                "success": True,
                "filename": validation["filename"],
                "file_id": file_id,
                "url": s3_url,
                "file_type": validation["file_type"],
                "file_size": validation["size"],
                "description": description,
                "error": None,
            }

        except Exception as e:
            logger.error(f"Error uploading file to S3: {e}")
            return {
                "success": False,
                "filename": validation["filename"],
                "error": f"Upload failed: {str(e)}",
                "url": None,
                "file_id": None,
                "description": None,
            }
