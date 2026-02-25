from __future__ import annotations

import asyncio
from typing import Optional, Dict, Any, List

import httpx

from src.common.s3_client import get_s3_uploader
from src.database.postgres.repositories.media_assets_queries import (
    get_fb_media_asset,
    upsert_fb_media_asset,
)
from src.database.postgres.utils import get_current_timestamp_ms
from src.utils.logger import get_logger

logger = get_logger()


class MediaMirrorService:
    """Handles mirroring Facebook-hosted media into S3 and tracking metadata."""

    # Retention window mappings (milliseconds) - aligned with schema constraint
    # Valid values: 'one_day', 'one_week', 'two_weeks', 'one_month', 'permanent'
    RETENTION_WINDOWS = {
        "one_day": 1 * 24 * 60 * 60 * 1000,  # 1 day
        "one_week": 7 * 24 * 60 * 60 * 1000,  # 7 days
        "two_weeks": 14 * 24 * 60 * 60 * 1000,  # 14 days
        "one_month": 30 * 24 * 60 * 60 * 1000,  # 30 days
    }  # 'permanent' returns None (no expiry)

    def __init__(self):
        self.s3_uploader = get_s3_uploader()

    @staticmethod
    def _is_asset_active(asset: Optional[Dict[str, Any]]) -> bool:
        if not asset:
            return False
        if asset.get("status") != "ready":
            return False
        expires_at = asset.get("expires_at")
        if expires_at is None:
            return True
        try:
            return int(expires_at) > get_current_timestamp_ms()
        except (TypeError, ValueError):
            return False

    @classmethod
    def _is_persistent(cls, retention_policy: str) -> bool:
        return retention_policy == "permanent"

    @classmethod
    def _compute_expires_at(cls, retention_policy: str) -> Optional[int]:
        if retention_policy == "permanent":
            return None
        window = cls.RETENTION_WINDOWS.get(retention_policy)
        if not window:
            return None
        return get_current_timestamp_ms() + window

    def _to_media_payload(
        self, asset: Dict[str, Any], original_url: Optional[str]
    ) -> Dict[str, Any]:
        # Convert UUID to string if present
        media_id_raw = asset.get("id")
        media_id = None
        if media_id_raw is not None:
            media_id = (
                str(media_id_raw)
                if hasattr(media_id_raw, "__str__")
                else media_id_raw
            )
        
        return {
            "id": media_id,
            "s3_url": asset.get("s3_url"),
            "status": asset.get("status"),
            "retention_policy": asset.get("retention_policy"),
            "expires_at": asset.get("expires_at"),
            "error": asset.get("error_message"),
            "original_url": original_url,
            "description": asset.get("description"),
            "description_model": asset.get("description_model"),
        }

    async def batch_ensure_media_assets(
        self,
        conn,
        items: List[Dict[str, Any]],
    ) -> List[Optional[Dict[str, Any]]]:
        """
        Batch ensure multiple media assets with parallel S3 uploads.

        Args:
            conn: Database connection
            items: List of dicts with keys:
                - user_id: str
                - owner_type: str
                - owner_id: str
                - field_name: str
                - original_url: str
                - retention_policy: str

        Returns:
            List of media payloads (same order as input items)
        """
        if not items:
            return []

        # Step 1: Check DB for existing assets (sequential due to asyncpg)
        existing_assets: List[Optional[Dict[str, Any]]] = []
        items_needing_upload: List[tuple[int, Dict[str, Any]]] = []

        for idx, item in enumerate(items):
            if not item.get("original_url"):
                existing_assets.append(None)
                continue

            normalized_owner_id = str(item["owner_id"])
            asset = await get_fb_media_asset(
                conn, item["owner_type"], normalized_owner_id, item["field_name"]
            )

            # If asset exists and is active or failed, use it
            if asset and asset.get("status") == "failed":
                existing_assets.append(
                    self._to_media_payload(asset, item["original_url"])
                )
                continue

            if self._is_asset_active(asset):
                existing_assets.append(
                    self._to_media_payload(asset, item["original_url"])
                )
                continue

            # Need to upload this one
            existing_assets.append(None)  # Placeholder, will be replaced
            items_needing_upload.append((idx, item))

        if not items_needing_upload:
            return existing_assets

        # Step 2: Download and upload to S3 in parallel
        logger.info(f"Batch uploading {len(items_needing_upload)} media items to S3")

        async def download_and_upload(
            item: Dict[str, Any],
        ) -> tuple[Optional[str], int, Optional[str]]:
            """Download image and upload to S3. Returns (s3_url, file_size, error)."""
            original_url = item["original_url"]
            retention_policy = item.get("retention_policy", "permanent")

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(original_url)
                    response.raise_for_status()
                    file_size = len(response.content)

                s3_url = await self.s3_uploader.upload_image_from_url(
                    original_url,
                    persistent=self._is_persistent(retention_policy),
                    retention_policy=retention_policy,
                )
                return (s3_url, file_size, None)
            except httpx.HTTPStatusError as exc:
                return (None, 0, f"http_{exc.response.status_code}")
            except Exception as exc:
                logger.error(f"Failed to upload media to S3: {exc}")
                return (None, 0, "download_failed")

        # Use semaphore to limit concurrent uploads
        semaphore = asyncio.Semaphore(5)

        async def upload_with_semaphore(
            item: Dict[str, Any],
        ) -> tuple[Optional[str], int, Optional[str]]:
            async with semaphore:
                return await download_and_upload(item)

        # Run all uploads in parallel
        upload_tasks = [upload_with_semaphore(item) for _, item in items_needing_upload]
        upload_results = await asyncio.gather(*upload_tasks, return_exceptions=True)

        # Step 3: Update DB with results (sequential due to asyncpg)
        for (idx, item), result in zip(items_needing_upload, upload_results):
            normalized_owner_id = str(item["owner_id"])
            original_url = item["original_url"]
            retention_policy = item.get("retention_policy", "permanent")

            if isinstance(result, Exception):
                logger.error(f"Exception during upload: {result}")
                s3_url, file_size, error = None, 0, "upload_exception"
            else:
                s3_url, file_size, error = result

            if not s3_url:
                # Upload failed
                db_asset = await upsert_fb_media_asset(
                    conn,
                    user_id=item["user_id"],
                    fb_owner_type=item["owner_type"],
                    fb_owner_id=normalized_owner_id,
                    fb_field_name=item["field_name"],
                    source_url=original_url,
                    source_hash=None,
                    media_type="image",
                    s3_key="",
                    s3_url="",
                    file_size_bytes=0,
                    status="failed",
                    retention_policy=retention_policy,
                    expires_at=None,
                    metadata=None,
                    error_message=error or "download_failed",
                )
                existing_assets[idx] = self._to_media_payload(db_asset, original_url)
            else:
                # Upload succeeded
                s3_key = self.s3_uploader._extract_s3_key_from_url(s3_url) or ""
                expires_at = self._compute_expires_at(retention_policy)

                db_asset = await upsert_fb_media_asset(
                    conn,
                    user_id=item["user_id"],
                    fb_owner_type=item["owner_type"],
                    fb_owner_id=normalized_owner_id,
                    fb_field_name=item["field_name"],
                    source_url=original_url,
                    source_hash=None,
                    media_type="image",
                    s3_key=s3_key,
                    s3_url=s3_url,
                    file_size_bytes=file_size,
                    status="ready",
                    retention_policy=retention_policy,
                    expires_at=expires_at,
                    metadata=None,
                )
                existing_assets[idx] = self._to_media_payload(db_asset, original_url)

        successful = sum(1 for a in existing_assets if a and a.get("status") == "ready")
        logger.info(
            f"Batch upload completed: {successful}/{len(items)} media items ready"
        )

        return existing_assets
