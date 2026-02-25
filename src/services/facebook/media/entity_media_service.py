"""Service for mirroring and describing media for Facebook entities.

Handles the complete workflow of:
1. Looking up entities in database
2. Extracting media URLs
3. Mirroring media to S3
4. Generating AI descriptions
"""

from typing import Any, Dict, Optional, List, Tuple
import asyncpg

from src.services.media.media_mirror_service import MediaMirrorService
from src.services.media.media_description_service import MediaDescriptionService
from src.database.postgres.repositories.media_assets_queries import (
    get_fb_media_asset,
    get_fb_media_assets_batch,
    update_media_description_by_id,
)
from src.database.postgres.executor import execute_async_single
from src.database.postgres.utils import get_current_timestamp_ms
from src.utils.logger import get_logger

logger = get_logger()


class EntityMediaService:
    """Service for processing media for Facebook entities."""

    def __init__(
        self,
        mirror_service: MediaMirrorService = None,
        description_service: MediaDescriptionService = None,
    ):
        self.mirror_service = mirror_service or MediaMirrorService()
        self.description_service = description_service or MediaDescriptionService()

    def media_is_active(self, media: Optional[Dict[str, Any]]) -> bool:
        """Check if media asset is active (ready and not expired)."""
        if not media or media.get("status") != "ready":
            return False
        expires_at = media.get("expires_at")
        if expires_at is None:
            return True
        try:
            return int(expires_at) > get_current_timestamp_ms()
        except (TypeError, ValueError):
            return False

    def _media_failed(self, media: Optional[Dict[str, Any]]) -> bool:
        """Check if media asset has failed."""
        return bool(media) and media.get("status") == "failed"

    async def lookup_entity_media(
        self, conn: asyncpg.Connection, owner_type: str, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        """Lookup entity in database and extract media URLs."""
        owner_id_str = str(owner_id)

        if owner_type == "fan_page":
            return await self._lookup_fan_page_media(conn, owner_id_str)
        elif owner_type == "page_scope_user":
            return await self._lookup_page_scope_user_media(conn, owner_id_str)
        elif owner_type == "post":
            return await self._lookup_post_media(conn, owner_id_str)
        elif owner_type == "comment":
            return await self._lookup_comment_media(conn, owner_id_str)
        elif owner_type == "message":
            return await self._lookup_message_media(conn, owner_id_str)
        elif owner_type == "facebook_conversation":
            return await self._lookup_conversation_media(conn, owner_id_str)
        else:
            logger.warning(f"Unknown owner_type: {owner_type}")
            return None

    async def _lookup_fan_page_media(
        self, conn: asyncpg.Connection, page_id: str
    ) -> Optional[Dict[str, Any]]:
        """Lookup fan page media."""
        query = """
            SELECT id, avatar, cover
            FROM fan_pages
            WHERE id = $1
        """
        result = await execute_async_single(conn, query, page_id)
        if not result:
            return None

        media_items = []
        if result.get("avatar"):
            media_items.append(
                {
                    "field_name": "avatar",
                    "original_url": result.get("avatar"),
                    "retention_policy": "permanent",
                }
            )
        if result.get("cover"):
            media_items.append(
                {
                    "field_name": "cover",
                    "original_url": result.get("cover"),
                    "retention_policy": "one_week",
                }
            )
        return {"media_items": media_items, "entity_data": result}

    async def _lookup_page_scope_user_media(
        self, conn: asyncpg.Connection, psu_id: str
    ) -> Optional[Dict[str, Any]]:
        """Lookup page scope user media."""
        query = """
            SELECT id, user_info
            FROM facebook_page_scope_users
            WHERE id = $1
        """
        result = await execute_async_single(conn, query, psu_id)
        if not result:
            return None

        user_info = result.get("user_info") or {}
        profile_pic = (
            user_info.get("profile_pic") if isinstance(user_info, dict) else None
        )

        media_items = []
        if profile_pic:
            media_items.append(
                {
                    "field_name": "profile_pic",
                    "original_url": profile_pic,
                    "retention_policy": "permanent",
                }
            )
        return {"media_items": media_items, "entity_data": result}

    async def _lookup_post_media(
        self, conn: asyncpg.Connection, post_id: str
    ) -> Optional[Dict[str, Any]]:
        """Lookup post media."""
        query = """
            SELECT id, photo_link, full_picture
            FROM posts
            WHERE id = $1
        """
        result = await execute_async_single(conn, query, post_id)
        if not result:
            return None

        # Use photo_link, fallback to full_picture
        photo_url = result.get("photo_link") or result.get("full_picture")
        media_items = []
        if photo_url:
            media_items.append(
                {
                    "field_name": "photo_link",
                    "original_url": photo_url,
                    "retention_policy": "one_week",
                }
            )
        return {"media_items": media_items, "entity_data": result}

    async def _lookup_comment_media(
        self, conn: asyncpg.Connection, comment_id: str
    ) -> Optional[Dict[str, Any]]:
        """Lookup comment media."""
        query = """
            SELECT id, photo_url
            FROM comments
            WHERE id = $1
        """
        result = await execute_async_single(conn, query, comment_id)
        if not result:
            return None

        media_items = []
        if result.get("photo_url"):
            media_items.append(
                {
                    "field_name": "photo_url",
                    "original_url": result.get("photo_url"),
                    "retention_policy": "one_week",
                }
            )
        return {"media_items": media_items, "entity_data": result}

    async def _lookup_message_media(
        self, conn: asyncpg.Connection, message_id: str
    ) -> Optional[Dict[str, Any]]:
        """Lookup message media."""
        query = """
            SELECT id, photo_url
            FROM messages
            WHERE id = $1
        """
        result = await execute_async_single(conn, query, message_id)
        if not result:
            return None

        media_items = []
        if result.get("photo_url"):
            media_items.append(
                {
                    "field_name": "photo_url",
                    "original_url": result.get("photo_url"),
                    "retention_policy": "one_week",
                }
            )
        return {"media_items": media_items, "entity_data": result}

    async def _lookup_conversation_media(
        self, conn: asyncpg.Connection, conversation_id: str
    ) -> Optional[Dict[str, Any]]:
        """Lookup conversation media (ad_context only)."""
        query = """
            SELECT 
                fcm.id,
                fcm.ad_context
            FROM facebook_conversation_messages fcm
            WHERE fcm.id = $1
        """
        result = await execute_async_single(conn, query, conversation_id)
        if not result:
            return None

        media_items = []
        # Ad context photo only
        ad_context = result.get("ad_context")
        if ad_context and isinstance(ad_context, dict):
            ad_photo_url = ad_context.get("photo_url")
            if ad_photo_url:
                media_items.append(
                    {
                        "field_name": "ad_context_photo_url",
                        "original_url": ad_photo_url,
                        "retention_policy": "one_week",
                    }
                )
        return {"media_items": media_items, "entity_data": result}

    async def process_entities_batch(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        entities: List[Dict[str, str]],  # [{"owner_type": ..., "owner_id": ...}]
        force_describe: bool = False,
        api_key: Optional[str] = None,
        parent_agent_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Process multiple entities in one batch for optimal performance.

        Flow:
        1. Lookup all entities -> collect all media items
        2. Pre-fetch ALL existing assets (1 batch query)
        3. Filter: items need mirror vs already mirrored
        4. Batch mirror ALL items (1 call)
        5. Filter: items need describe
        6. Batch describe ALL items (1 call, parallel LLM inside)

        Returns:
            List of results, one per entity, with same structure as process_entity_media
        """
        if not entities:
            return []

        logger.info(
            f"Batch processing {len(entities)} entities for user {user_id}"
        )

        # Step 1: Lookup all entities and collect media items
        entity_media_map: Dict[str, Dict[str, Any]] = {}  # entity_key -> entity_data
        all_media_items: List[Dict[str, Any]] = []  # All media items with entity tracking

        for entity in entities:
            owner_type = entity.get("owner_type")
            owner_id = str(entity.get("owner_id"))
            entity_key = f"{owner_type}:{owner_id}"

            try:
                entity_data = await self.lookup_entity_media(conn, owner_type, owner_id)
                if not entity_data:
                    entity_media_map[entity_key] = {
                        "status": "failed",
                        "error": "Entity not found in database",
                        "media": [],
                        "owner_type": owner_type,
                        "owner_id": owner_id,
                    }
                    continue

                media_items = entity_data.get("media_items", [])
                if not media_items:
                    entity_media_map[entity_key] = {
                        "status": "completed",
                        "media": [],
                        "message": "No media found for this entity",
                        "owner_type": owner_type,
                        "owner_id": owner_id,
                    }
                    continue

                # Track media items with entity info
                for item in media_items:
                    effective_owner_type = item.get("owner_type_override", owner_type)
                    effective_owner_id = str(item.get("owner_id_override", owner_id))
                    all_media_items.append(
                        {
                            "entity_key": entity_key,
                            "owner_type": owner_type,
                            "owner_id": owner_id,
                            "effective_owner_type": effective_owner_type,
                            "effective_owner_id": effective_owner_id,
                            "field_name": item["field_name"],
                            "original_url": item["original_url"],
                            "retention_policy": item.get("retention_policy", "one_week"),
                        }
                    )

                # Initialize entity result
                entity_media_map[entity_key] = {
                    "status": "completed",
                    "media": [],
                    "owner_type": owner_type,
                    "owner_id": owner_id,
                }

            except Exception as e:
                logger.error(f"Error looking up entity {entity_key}: {str(e)}")
                entity_media_map[entity_key] = {
                    "status": "failed",
                    "error": f"Error looking up entity: {str(e)}",
                    "media": [],
                    "owner_type": owner_type,
                    "owner_id": owner_id,
                }

        if not all_media_items:
            logger.info("No media items found across all entities")
            return list(entity_media_map.values())

        logger.info(
            f"Found {len(all_media_items)} media items across {len(entity_media_map)} entities"
        )

        # Step 2: Pre-fetch ALL existing assets in one batch query
        batch_query_items: List[Tuple[str, str, str]] = [
            (item["effective_owner_type"], item["effective_owner_id"], item["field_name"])
            for item in all_media_items
        ]
        existing_assets_map = await get_fb_media_assets_batch(conn, batch_query_items)

        logger.info(
            f"Pre-fetched {len(existing_assets_map)} existing assets "
            f"({len(all_media_items) - len(existing_assets_map)} need mirror)"
        )

        # Step 3: Prepare items for mirroring
        items_to_mirror: List[Dict[str, Any]] = []
        items_already_mirrored: List[Dict[str, Any]] = []

        for item in all_media_items:
            asset_key = f"{item['effective_owner_type']}:{item['effective_owner_id']}:{item['field_name']}"
            existing_asset = existing_assets_map.get(asset_key)

            # Skip if media failed (don't retry)
            if self._media_failed(existing_asset):
                continue

            # Check if already has active S3 URL
            if existing_asset and self.media_is_active(existing_asset):
                s3_url = existing_asset.get("s3_url")
                if s3_url:
                    items_already_mirrored.append(
                        {
                            "entity_key": item["entity_key"],
                            "media_id": str(existing_asset.get("id")),
                            "s3_url": s3_url,
                            "field_name": item["field_name"],
                            "owner_type": item["owner_type"],
                            "owner_id": item["owner_id"],
                            "effective_owner_type": item["effective_owner_type"],
                            "effective_owner_id": item["effective_owner_id"],
                            "existing_description": existing_asset.get("description"),
                            "action": "skipped_mirror",
                        }
                    )
                    continue

            # Need to mirror
            items_to_mirror.append(
                {
                    "entity_key": item["entity_key"],
                    "user_id": user_id,
                    "owner_type": item["owner_type"],
                    "owner_id": item["owner_id"],
                    "effective_owner_type": item["effective_owner_type"],
                    "effective_owner_id": item["effective_owner_id"],
                    "field_name": item["field_name"],
                    "original_url": item["original_url"],
                    "retention_policy": item["retention_policy"],
                }
            )

        # Step 4: Batch mirror ALL items that need it
        mirrored_results = []
        if items_to_mirror:
            mirror_batch = [
                {
                    "user_id": item["user_id"],
                    "owner_type": item["effective_owner_type"],
                    "owner_id": item["effective_owner_id"],
                    "field_name": item["field_name"],
                    "original_url": item["original_url"],
                    "retention_policy": item["retention_policy"],
                }
                for item in items_to_mirror
            ]
            logger.info(f"Batch mirroring {len(mirror_batch)} media items")
            mirrored_results = await self.mirror_service.batch_ensure_media_assets(
                conn, mirror_batch
            )
            logger.info(f"Batch mirror completed: {len(mirrored_results)} items processed")

        # Step 5: Collect all media assets (mirrored + already existing)
        all_media_assets: List[Dict[str, Any]] = []

        # Process newly mirrored items
        for item, mirror_result in zip(items_to_mirror, mirrored_results):
            if mirror_result and mirror_result.get("status") == "ready":
                all_media_assets.append(
                    {
                        "entity_key": item["entity_key"],
                        "media_id": str(mirror_result.get("id")),
                        "s3_url": mirror_result.get("s3_url"),
                        "field_name": item["field_name"],
                        "owner_type": item["owner_type"],
                        "owner_id": item["owner_id"],
                        "effective_owner_type": item["effective_owner_type"],
                        "effective_owner_id": item["effective_owner_id"],
                        "action": "mirrored",
                    }
                )

        # Process already mirrored items
        all_media_assets.extend(items_already_mirrored)

        # Step 6: Filter items that need descriptions
        items_to_describe: List[Dict[str, Any]] = []
        for media_asset in all_media_assets:
            s3_url = media_asset.get("s3_url")
            if not s3_url:
                media_asset["action"] = "failed"
                media_asset["error"] = "No S3 URL available"
                continue

            # Check if already has description
            has_description = bool(media_asset.get("existing_description"))
            if has_description and not force_describe:
                # Skip description
                media_asset["description"] = media_asset.get("existing_description")
                media_asset["action"] = (
                    "skipped"
                    if media_asset["action"] == "skipped_mirror"
                    else "mirrored"
                )
            else:
                # Need to describe
                context_label = (
                    f"{media_asset['effective_owner_type']} {media_asset['field_name']}"
                )
                items_to_describe.append(
                    {
                        "entity_key": media_asset["entity_key"],
                        "media_id": media_asset["media_id"],
                        "url": s3_url,
                        "context": context_label,
                        "field_name": media_asset["field_name"],
                        "effective_owner_type": media_asset["effective_owner_type"],
                        "effective_owner_id": media_asset["effective_owner_id"],
                        "owner_type": media_asset["owner_type"],
                        "owner_id": media_asset["owner_id"],
                    }
                )

        # Step 7: Batch describe ALL items (parallel LLM inside)
        if items_to_describe and api_key:
            # Final check: fetch latest descriptions from DB to prevent duplicates
            # (in case another process updated them)
            final_check_items: List[Tuple[str, str, str]] = [
                (item["effective_owner_type"], item["effective_owner_id"], item["field_name"])
                for item in items_to_describe
            ]
            final_assets_map = await get_fb_media_assets_batch(conn, final_check_items)

            items_with_ids = []
            for item in items_to_describe:
                asset_key = f"{item['effective_owner_type']}:{item['effective_owner_id']}:{item['field_name']}"
                db_media = final_assets_map.get(asset_key)
                if db_media:
                    # Check if already described in DB
                    if db_media.get("description") and not force_describe:
                        logger.debug(
                            f"Media {db_media.get('id')} already has description, skipping"
                        )
                        # Update result with existing description
                        for media_asset in all_media_assets:
                            if media_asset["media_id"] == item["media_id"]:
                                media_asset["description"] = db_media.get("description")
                                media_asset["action"] = (
                                    "skipped"
                                    if media_asset["action"] == "skipped_mirror"
                                    else "mirrored"
                                )
                                break
                        continue
                    item["media_id"] = str(db_media.get("id"))
                    items_with_ids.append(item)

            if items_with_ids:
                describe_items = [
                    {
                        "media_id": item["media_id"],
                        "url": item["url"],
                        "context": item["context"],
                    }
                    for item in items_with_ids
                ]

                logger.info(f"Batch describing {len(describe_items)} media items (parallel LLM)")
                descriptions = await self.description_service.describe_batch(
                    conn=conn,
                    items=describe_items,
                    api_key=api_key,
                    user_id=user_id,
                    parent_agent_response_id=parent_agent_response_id,
                    conversation_id=conversation_id,
                    branch_id=branch_id,
                )
                logger.info(f"Batch describe completed: {len(descriptions)} descriptions generated")

                # Update media assets with descriptions
                for item in items_with_ids:
                    media_id = item["media_id"]
                    description = descriptions.get(media_id)

                    if description:
                        # Update database
                        await update_media_description_by_id(
                            conn,
                            media_id,
                            description,
                            self.description_service.model,
                        )

                        # Update result
                        for media_asset in all_media_assets:
                            if media_asset["media_id"] == media_id:
                                media_asset["description"] = description
                                if media_asset["action"] == "mirrored":
                                    media_asset["action"] = "mirrored_and_described"
                                elif media_asset["action"] == "skipped_mirror":
                                    media_asset["action"] = "described"
                                break
                    else:
                        # Description failed
                        for media_asset in all_media_assets:
                            if media_asset["media_id"] == media_id:
                                media_asset["action"] = "failed"
                                media_asset["error"] = "Failed to generate description"
                                break

        # Step 8: Group media assets by entity and format results
        for entity_key, entity_result in entity_media_map.items():
            if entity_result["status"] != "completed":
                continue

            entity_media = [
                {
                    "field_name": media_asset["field_name"],
                    "media_id": media_asset["media_id"],
                    "s3_url": media_asset.get("s3_url"),
                    "description": media_asset.get("description"),
                    "action": media_asset["action"],
                    "error": media_asset.get("error"),
                }
                for media_asset in all_media_assets
                if media_asset["entity_key"] == entity_key
            ]
            entity_result["media"] = entity_media

        return list(entity_media_map.values())

    async def process_entity_media(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        owner_type: str,
        owner_id: str,
        force_describe: bool = False,
        api_key: Optional[str] = None,
        parent_agent_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process media for a single entity: lookup, mirror, and describe.

        Returns:
            {
                "status": "completed" | "failed",
                "media": [...],
                "error": "...",
                "message": "..."
            }
        """
        try:
            # Lookup entity and extract media URLs
            entity_data = await self.lookup_entity_media(conn, owner_type, owner_id)

            if not entity_data:
                return {
                    "status": "failed",
                    "error": "Entity not found in database",
                    "media": [],
                }

            media_items = entity_data.get("media_items", [])

            if not media_items:
                return {
                    "status": "completed",
                    "media": [],
                    "message": "No media found for this entity",
                }

            # Prepare batch items for mirroring
            batch_items = []
            for item in media_items:
                # Use override if provided (for conversation entities)
                effective_owner_type = item.get("owner_type_override", owner_type)
                effective_owner_id = str(item.get("owner_id_override", owner_id))

                # Check if already mirrored
                existing_asset = await get_fb_media_asset(
                    conn,
                    effective_owner_type,
                    effective_owner_id,
                    item["field_name"],
                )

                # Skip if media failed (don't retry)
                if self._media_failed(existing_asset):
                    continue

                # Skip if already has active S3 URL (ready and not expired)
                if existing_asset and self.media_is_active(existing_asset):
                    s3_url = existing_asset.get("s3_url")
                    if s3_url:
                        # Already mirrored, just need to describe if needed
                        batch_items.append(
                            {
                                "media_id": str(existing_asset.get("id")),
                                "s3_url": s3_url,
                                "field_name": item["field_name"],
                                "owner_type": effective_owner_type,
                                "owner_id": effective_owner_id,
                                "already_mirrored": True,
                                "existing_description": existing_asset.get(
                                    "description"
                                ),
                            }
                        )
                        continue

                # Need to mirror
                batch_items.append(
                    {
                        "user_id": user_id,
                        "owner_type": effective_owner_type,
                        "owner_id": effective_owner_id,
                        "field_name": item["field_name"],
                        "original_url": item["original_url"],
                        "retention_policy": item.get("retention_policy", "one_week"),
                        "already_mirrored": False,
                    }
                )

            # Separate items that need mirroring vs already mirrored
            items_to_mirror = [
                item for item in batch_items if not item.get("already_mirrored", False)
            ]
            items_already_mirrored = [
                item for item in batch_items if item.get("already_mirrored", False)
            ]

            # Mirror items that need it
            mirrored_results = []
            if items_to_mirror:
                mirror_batch = [
                    {
                        "user_id": item["user_id"],
                        "owner_type": item["owner_type"],
                        "owner_id": item["owner_id"],
                        "field_name": item["field_name"],
                        "original_url": item["original_url"],
                        "retention_policy": item["retention_policy"],
                    }
                    for item in items_to_mirror
                ]
                mirrored_results = await self.mirror_service.batch_ensure_media_assets(
                    conn, mirror_batch
                )

            # Collect all media assets (mirrored + already existing)
            all_media_assets = []

            # Process newly mirrored items
            for item, mirror_result in zip(items_to_mirror, mirrored_results):
                if mirror_result and mirror_result.get("status") == "ready":
                    media_id = str(mirror_result.get("id"))
                    s3_url = mirror_result.get("s3_url")
                    all_media_assets.append(
                        {
                            "media_id": media_id,
                            "s3_url": s3_url,
                            "field_name": item["field_name"],
                            "owner_type": item["owner_type"],
                            "owner_id": item["owner_id"],
                            "action": "mirrored",
                        }
                    )

            # Process already mirrored items
            for item in items_already_mirrored:
                media_id = item["media_id"]
                s3_url = item["s3_url"]
                all_media_assets.append(
                    {
                        "media_id": media_id,
                        "s3_url": s3_url,
                        "field_name": item["field_name"],
                        "owner_type": item["owner_type"],
                        "owner_id": item["owner_id"],
                        "action": "skipped_mirror",
                        "existing_description": item.get("existing_description"),
                    }
                )

            # Now describe media that need descriptions
            items_to_describe = []
            for media_asset in all_media_assets:
                media_id = media_asset["media_id"]
                s3_url = media_asset["s3_url"]

                if not s3_url:
                    media_asset["action"] = "failed"
                    media_asset["error"] = "No S3 URL available"
                    continue

                # Check if already has description
                has_description = bool(media_asset.get("existing_description"))
                if not has_description:
                    # Check in DB
                    existing_asset = await get_fb_media_asset(
                        conn,
                        media_asset["owner_type"],
                        media_asset["owner_id"],
                        media_asset["field_name"],
                    )
                    has_description = bool(
                        existing_asset and existing_asset.get("description")
                    )
                    if has_description:
                        media_asset["existing_description"] = existing_asset.get(
                            "description"
                        )

                if has_description and not force_describe:
                    # Skip description
                    media_asset["description"] = media_asset.get("existing_description")
                    media_asset["action"] = (
                        "skipped"
                        if media_asset["action"] == "skipped_mirror"
                        else "mirrored"
                    )
                else:
                    # Need to describe
                    context_label = (
                        f"{media_asset['owner_type']} {media_asset['field_name']}"
                    )
                    items_to_describe.append(
                        {
                            "media_id": media_id,
                            "url": s3_url,
                            "context": context_label,
                            "field_name": media_asset["field_name"],
                            "owner_type": media_asset["owner_type"],
                            "owner_id": media_asset["owner_id"],
                        }
                    )

            # Batch describe
            if items_to_describe and api_key:
                # IMPORTANT: Check if already described in DB (prevents duplicate LLM calls)
                # This handles race conditions where multiple requests process same media
                items_with_ids = []
                for item in items_to_describe:
                    db_media = await get_fb_media_asset(
                        conn,
                        item["owner_type"],
                        item["owner_id"],
                        item["field_name"],
                    )
                    if db_media:
                        # Check if already described in DB
                        if db_media.get("description") and not force_describe:
                            logger.debug(
                                f"Media {db_media.get('id')} already has description, skipping"
                            )
                            # Update result with existing description
                            for media_asset in all_media_assets:
                                if media_asset["media_id"] == item["media_id"]:
                                    media_asset["description"] = db_media.get(
                                        "description"
                                    )
                                    media_asset["action"] = (
                                        "skipped"
                                        if media_asset["action"] == "skipped_mirror"
                                        else "mirrored"
                                    )
                                    break
                            continue
                        item["media_id"] = str(db_media.get("id"))
                        items_with_ids.append(item)

                if items_with_ids:
                    describe_items = [
                        {
                            "media_id": item["media_id"],
                            "url": item["url"],
                            "context": item["context"],
                        }
                        for item in items_with_ids
                    ]

                    descriptions = await self.description_service.describe_batch(
                        conn=conn,
                        items=describe_items,
                        api_key=api_key,
                        user_id=user_id,
                        parent_agent_response_id=parent_agent_response_id,
                        conversation_id=conversation_id,
                        branch_id=branch_id,
                    )

                    # Update media assets with descriptions
                    for item in items_with_ids:
                        media_id = item["media_id"]
                        description = descriptions.get(media_id)

                        if description:
                            # Update database
                            await update_media_description_by_id(
                                conn,
                                media_id,
                                description,
                                self.description_service.model,
                            )

                            # Update result
                            for media_asset in all_media_assets:
                                if media_asset["media_id"] == media_id:
                                    media_asset["description"] = description
                                    if media_asset["action"] == "mirrored":
                                        media_asset["action"] = "mirrored_and_described"
                                    elif media_asset["action"] == "skipped_mirror":
                                        media_asset["action"] = "described"
                                    break
                        else:
                            # Description failed
                            for media_asset in all_media_assets:
                                if media_asset["media_id"] == media_id:
                                    media_asset["action"] = "failed"
                                    media_asset["error"] = (
                                        "Failed to generate description"
                                    )
                                    break

            # Format final media results
            final_media = []
            for media_asset in all_media_assets:
                final_media.append(
                    {
                        "field_name": media_asset["field_name"],
                        "media_id": media_asset["media_id"],
                        "s3_url": media_asset.get("s3_url"),
                        "description": media_asset.get("description"),
                        "action": media_asset["action"],
                        "error": media_asset.get("error"),
                    }
                )

            return {
                "status": "completed",
                "media": final_media,
            }

        except Exception as e:
            logger.error(f"Error processing entity {owner_type}:{owner_id}: {str(e)}")
            return {
                "status": "failed",
                "error": f"Internal error: {str(e)}",
                "media": [],
            }
