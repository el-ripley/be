from typing import Any, Dict, List, Optional
import json

from src.database.postgres.utils import get_current_timestamp_ms
from src.services.media.media_mirror_service import MediaMirrorService
from src.services.media.media_description_service import MediaDescriptionService
from src.database.postgres.repositories.media_assets_queries import (
    update_media_description_by_id,
    get_fb_media_asset,
)
from src.utils.logger import get_logger

logger = get_logger()


def _ensure_dict(data: Any) -> Optional[Dict[str, Any]]:
    """Convert data to dict if possible."""
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


class MediaAssetService:
    """
    Service for managing Facebook media assets (avatars, photos, etc.).
    Handles media mirroring, validation, and AI description generation.
    """

    def __init__(self):
        self.media_mirror_service = MediaMirrorService()
        self.description_service = MediaDescriptionService()

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

    def media_has_source(self, media: Optional[Dict[str, Any]]) -> bool:
        """Check if media has a valid source URL."""
        media_dict = _ensure_dict(media)
        if not media_dict:
            return False
        for key in ("s3_url", "original_url", "url"):
            value = media_dict.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return False

    async def ensure_conversation_assets(
        self,
        conn,
        user_id: str,
        fb_conversation_id: str,
        fb_data: Dict[str, Any],
        should_describe: bool = False,
        user_api_key: Optional[str] = None,
        parent_agent_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> None:
        """
        Ensure all media assets for a conversation are mirrored and available.

        Args:
            conn: Database connection
            user_id: User ID who owns this media
            fb_conversation_id: Facebook conversation/thread ID
            fb_data: Facebook conversation data
            should_describe: If True, generate AI descriptions for unsettled media
            user_api_key: System OpenAI API key (required if should_describe=True)
            parent_agent_response_id: Parent agent_response ID for cost tracking
            conversation_id: OpenAI conversation ID for cost tracking (UUID)
            branch_id: Branch ID for cost tracking
        """
        if not fb_data:
            return

        # Add conversation_id to fb_data for ad_context media processing
        if fb_conversation_id:
            fb_data["_conversation_id"] = fb_conversation_id

        # 1. Mirror media to S3
        await self._populate_conversation_media(conn, user_id, fb_data)

        # 2. If should_describe=True, describe unsettled media
        if should_describe:
            if not user_api_key:
                logger.warning(
                    f"Cannot describe media for user {user_id}: API key not provided"
                )
            else:
                await self._describe_unsettled_media(
                    conn,
                    fb_data,
                    user_api_key,
                    user_id=user_id,
                    parent_agent_response_id=parent_agent_response_id,
                    conversation_id=conversation_id,
                    branch_id=branch_id,
                )

    async def ensure_comment_assets(
        self,
        conn,
        user_id: str,
        root_comment_id: str,
        fb_data: Dict[str, Any],
        should_describe: bool = False,
        user_api_key: Optional[str] = None,
        parent_agent_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> None:
        """
        Ensure all media assets for a comment thread are mirrored and available.

        Args:
            conn: Database connection
            user_id: User ID who owns this media
            root_comment_id: Root comment ID
            fb_data: Facebook comment thread data
            should_describe: If True, generate AI descriptions for unsettled media
            user_api_key: System OpenAI API key (required if should_describe=True)
            parent_agent_response_id: Parent agent_response ID for cost tracking
            conversation_id: Conversation ID for cost tracking
            branch_id: Branch ID for cost tracking
        """
        if not fb_data:
            return

        # 1. Mirror media to S3
        await self._populate_comment_media(conn, user_id, fb_data)

        # 2. If should_describe=True, describe unsettled media
        if should_describe:
            if not user_api_key:
                logger.warning(
                    f"Cannot describe media for user {user_id}: API key not provided"
                )
            else:
                await self._describe_unsettled_media(
                    conn,
                    fb_data,
                    user_api_key,
                    user_id=user_id,
                    parent_agent_response_id=parent_agent_response_id,
                    conversation_id=conversation_id,
                    branch_id=branch_id,
                )

    async def ensure_post_assets(
        self,
        conn,
        user_id: str,
        post_id: str,
        post_data: Dict[str, Any],
        should_describe: bool = False,
        user_api_key: Optional[str] = None,
        parent_agent_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> None:
        """
        Ensure all media assets for a post are mirrored and available.

        Args:
            conn: Database connection
            user_id: User ID who owns this media
            post_id: Post ID
            post_data: Post data dictionary (will be modified in place)
            should_describe: If True, generate AI descriptions for unsettled media
            user_api_key: System OpenAI API key (required if should_describe=True)
            parent_agent_response_id: Parent agent_response ID for cost tracking
            conversation_id: OpenAI conversation ID for cost tracking (UUID)
            branch_id: Branch ID for cost tracking
        """
        if not post_data:
            return

        # 1. Mirror media to S3
        await self._populate_post_media(conn, user_id, post_data)

        # 2. If should_describe=True, describe unsettled media
        if should_describe:
            if not user_api_key:
                logger.warning(
                    f"Cannot describe media for user {user_id}: API key not provided"
                )
            else:
                # Wrap post_data in fb_data structure for _describe_unsettled_media
                fb_data = {"post": post_data}
                await self._describe_unsettled_media(
                    conn,
                    fb_data,
                    user_api_key,
                    user_id=user_id,
                    parent_agent_response_id=parent_agent_response_id,
                    conversation_id=conversation_id,
                    branch_id=branch_id,
                )

    def _media_failed(self, media: Optional[Dict[str, Any]]) -> bool:
        """Check if media asset has failed."""
        return bool(media) and media.get("status") == "failed"

    def is_media_settled(self, media: Optional[Dict[str, Any]]) -> bool:
        """
        Check if media has reached final state (settled).

        Media is settled if:
        - It has a description (AI-generated or placeholder), OR
        - It has failed status (which means we have a placeholder)

        Args:
            media: Media dict from database or payload

        Returns:
            True if media is settled, False otherwise
        """
        if not media:
            return False
        # Settled = has description OR has failed status with placeholder
        has_description = bool(media.get("description"))
        is_failed = media.get("status") == "failed"
        return has_description or is_failed

    async def _populate_conversation_media(
        self, conn, user_id: str, fb_data: Dict[str, Any]
    ) -> bool:
        """Populate media assets for conversation data using batch upload."""
        if not fb_data:
            return False

        page_info = fb_data.get("page_info") or {}
        user_info = fb_data.get("user_info") or {}
        items = fb_data.get("items", [])

        # Collect all media items to process
        batch_items: List[Dict[str, Any]] = []
        item_targets: List[tuple] = []  # (target, media_attr, url_attr)

        # Page avatar
        if page_info and fb_data.get("fan_page_id"):
            original_url = self._get_original_url(page_info, "avatar_media", "avatar")
            if original_url:
                batch_items.append(
                    {
                        "user_id": user_id,
                        "owner_type": "fan_page",
                        "owner_id": fb_data.get("fan_page_id"),
                        "field_name": "avatar",
                        "original_url": original_url,
                        "retention_policy": "permanent",
                    }
                )
                item_targets.append((page_info, "avatar_media", "avatar"))

        # Page cover
        if page_info and fb_data.get("fan_page_id"):
            original_url = self._get_original_url(page_info, "cover_media", "cover")
            if original_url:
                batch_items.append(
                    {
                        "user_id": user_id,
                        "owner_type": "fan_page",
                        "owner_id": fb_data.get("fan_page_id"),
                        "field_name": "cover",
                        "original_url": original_url,
                        "retention_policy": "one_week",
                    }
                )
                item_targets.append((page_info, "cover_media", "cover"))

        # User avatar
        psu_id = user_info.get("id") or fb_data.get("facebook_page_scope_user_id")
        if user_info and psu_id:
            original_url = self._get_original_url(user_info, "avatar_media", "avatar")
            if original_url:
                batch_items.append(
                    {
                        "user_id": user_id,
                        "owner_type": "page_scope_user",
                        "owner_id": psu_id,
                        "field_name": "profile_pic",
                        "original_url": original_url,
                        "retention_policy": "permanent",
                    }
                )
                item_targets.append((user_info, "avatar_media", "avatar"))

        # Message photos
        for item in items:
            if item.get("id"):
                original_url = self._get_original_url(item, "photo_media", "photo_url")
                if original_url:
                    batch_items.append(
                        {
                            "user_id": user_id,
                            "owner_type": "message",
                            "owner_id": item.get("id"),
                            "field_name": "photo_url",
                            "original_url": original_url,
                            "retention_policy": "one_week",
                        }
                    )
                    item_targets.append((item, "photo_media", "photo_url"))

        # Ad context photo_url (user replied to a Facebook ad)
        ad_context = fb_data.get("ad_context")
        if ad_context and isinstance(ad_context, dict):
            # Use conversation_id as owner_id for ad_context media
            conversation_id = fb_data.get("_conversation_id") or ""
            if not conversation_id:
                # Fallback: construct from page_id and user_id
                conversation_id = (
                    fb_data.get("fan_page_id", "")
                    + "_"
                    + fb_data.get("facebook_page_scope_user_id", "")
                )
            # Field name will be "ad_context_photo_url" to differentiate from other media
            original_url = self._get_original_url(
                ad_context, "photo_media", "photo_url"
            )
            if original_url:
                batch_items.append(
                    {
                        "user_id": user_id,
                        "owner_type": "facebook_conversation",
                        "owner_id": conversation_id,
                        "field_name": "ad_context_photo_url",
                        "original_url": original_url,
                        "retention_policy": "one_week",
                    }
                )
                item_targets.append((ad_context, "photo_media", "photo_url"))

        if not batch_items:
            return False

        # Batch upload all media in parallel
        results = await self.media_mirror_service.batch_ensure_media_assets(
            conn, batch_items
        )

        # Apply results to fb_data
        updated = False
        for (target, media_attr, url_attr), media_payload, item in zip(
            item_targets, results, batch_items
        ):
            if media_payload:
                media_payload.setdefault("retention_policy", item["retention_policy"])
                media_payload["original_url"] = item["original_url"]
                target[media_attr] = media_payload
                if (
                    media_payload.get("s3_url")
                    and media_payload.get("status") == "ready"
                ):
                    target[url_attr] = media_payload["s3_url"]
                else:
                    target[url_attr] = None
                updated = True
            elif not target.get(media_attr):
                # Set failed status if no result
                target[media_attr] = {
                    "status": "failed",
                    "retention_policy": item["retention_policy"],
                    "original_url": item["original_url"],
                }
                target[url_attr] = None

        return updated

    def _get_original_url(
        self, target: Dict[str, Any], media_attr: str, url_attr: str
    ) -> Optional[str]:
        """Extract original URL from target, checking media and url attributes."""
        current_media = target.get(media_attr)

        # If media is failed, skip
        if self._media_failed(current_media):
            return None

        # If media is active (ready and not expired), no need to re-upload
        if self.media_is_active(current_media):
            return None

        # Get original URL
        original_url = None
        if isinstance(current_media, dict):
            original_url = current_media.get("original_url")
        if not original_url:
            original_url = target.get(url_attr)

        return original_url if original_url else None

    async def _populate_comment_media(
        self, conn, user_id: str, fb_data: Dict[str, Any]
    ) -> bool:
        """Populate media assets for comment thread data using batch upload."""
        if not fb_data:
            return False

        # Support both "page" and "page_info" keys (different services use different keys)
        page_info = fb_data.get("page") or fb_data.get("page_info") or {}
        post_info = fb_data.get("post") or {}
        comments = fb_data.get("comments", [])

        # Collect all media items to process
        batch_items: List[Dict[str, Any]] = []
        item_targets: List[tuple] = []  # (target, media_attr, url_attr)

        # Page avatar
        if page_info and page_info.get("id"):
            original_url = self._get_original_url(page_info, "avatar_media", "avatar")
            if original_url:
                batch_items.append(
                    {
                        "user_id": user_id,
                        "owner_type": "fan_page",
                        "owner_id": page_info.get("id"),
                        "field_name": "avatar",
                        "original_url": original_url,
                        "retention_policy": "permanent",
                    }
                )
                item_targets.append((page_info, "avatar_media", "avatar"))

        # Page cover
        if page_info and page_info.get("id"):
            original_url = self._get_original_url(page_info, "cover_media", "cover")
            if original_url:
                batch_items.append(
                    {
                        "user_id": user_id,
                        "owner_type": "fan_page",
                        "owner_id": page_info.get("id"),
                        "field_name": "cover",
                        "original_url": original_url,
                        "retention_policy": "one_week",
                    }
                )
                item_targets.append((page_info, "cover_media", "cover"))

        # Post photo
        if post_info and post_info.get("id"):
            original_url = self._get_original_url(
                post_info, "photo_media", "photo_link"
            )
            if original_url:
                batch_items.append(
                    {
                        "user_id": user_id,
                        "owner_type": "post",
                        "owner_id": post_info.get("id"),
                        "field_name": "photo_link",
                        "original_url": original_url,
                        "retention_policy": "one_week",
                    }
                )
                item_targets.append((post_info, "photo_media", "photo_link"))

        # Comment photos
        for comment in comments:
            if comment.get("id"):
                original_url = self._get_original_url(
                    comment, "photo_media", "photo_url"
                )
                if original_url:
                    batch_items.append(
                        {
                            "user_id": user_id,
                            "owner_type": "comment",
                            "owner_id": comment.get("id"),
                            "field_name": "photo_url",
                            "original_url": original_url,
                            "retention_policy": "one_week",
                        }
                    )
                    item_targets.append((comment, "photo_media", "photo_url"))

        if not batch_items:
            return False

        # Batch upload all media in parallel
        results = await self.media_mirror_service.batch_ensure_media_assets(
            conn, batch_items
        )

        # Apply results to fb_data
        updated = False
        for (target, media_attr, url_attr), media_payload, item in zip(
            item_targets, results, batch_items
        ):
            if media_payload:
                media_payload.setdefault("retention_policy", item["retention_policy"])
                media_payload["original_url"] = item["original_url"]
                target[media_attr] = media_payload
                if (
                    media_payload.get("s3_url")
                    and media_payload.get("status") == "ready"
                ):
                    target[url_attr] = media_payload["s3_url"]
                else:
                    target[url_attr] = None
                updated = True
            elif not target.get(media_attr):
                # Set failed status if no result
                target[media_attr] = {
                    "status": "failed",
                    "retention_policy": item["retention_policy"],
                    "original_url": item["original_url"],
                }
                target[url_attr] = None

        return updated

    async def _populate_post_media(
        self, conn, user_id: str, post_data: Dict[str, Any]
    ) -> bool:
        """Populate media assets for post data using batch upload."""
        if not post_data:
            return False

        # Collect all media items to process
        batch_items: List[Dict[str, Any]] = []
        item_targets: List[tuple] = []  # (target, media_attr, url_attr)

        # Post photo
        if post_data.get("id"):
            original_url = self._get_original_url(
                post_data, "photo_media", "photo_link"
            )
            if original_url:
                batch_items.append(
                    {
                        "user_id": user_id,
                        "owner_type": "post",
                        "owner_id": post_data.get("id"),
                        "field_name": "photo_link",
                        "original_url": original_url,
                        "retention_policy": "one_week",
                    }
                )
                item_targets.append((post_data, "photo_media", "photo_link"))

        if not batch_items:
            return False

        # Batch upload all media in parallel
        results = await self.media_mirror_service.batch_ensure_media_assets(
            conn, batch_items
        )

        # Apply results to post_data
        updated = False
        for (target, media_attr, url_attr), media_payload, item in zip(
            item_targets, results, batch_items
        ):
            if media_payload:
                media_payload.setdefault("retention_policy", item["retention_policy"])
                media_payload["original_url"] = item["original_url"]
                target[media_attr] = media_payload
                if (
                    media_payload.get("s3_url")
                    and media_payload.get("status") == "ready"
                ):
                    target[url_attr] = media_payload["s3_url"]
                else:
                    target[url_attr] = None
                updated = True
            elif not target.get(media_attr):
                # Set failed status if no result
                target[media_attr] = {
                    "status": "failed",
                    "retention_policy": item["retention_policy"],
                    "original_url": item["original_url"],
                }
                target[url_attr] = None

        return updated

    async def _describe_unsettled_media(
        self,
        conn,
        fb_data: Dict[str, Any],
        user_api_key: str,
        user_id: Optional[str] = None,
        parent_agent_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> None:
        """
        Generate descriptions for media that are not yet settled.

        Args:
            conn: Database connection
            fb_data: Facebook data with media fields
            user_api_key: System OpenAI API key
            user_id: User ID for cost tracking
            parent_agent_response_id: Parent agent_response ID for cost tracking
            conversation_id: Conversation ID for cost tracking
            branch_id: Branch ID for cost tracking
        """
        # Collect all unsettled media items
        items_to_describe: List[Dict[str, Any]] = []

        # Helper to collect media from a target dict
        def collect_media(
            target: Dict[str, Any],
            owner_type: str,
            owner_id: str,
            field_name: str,
            context_label: str,
        ):
            media = (
                target.get("avatar_media")
                or target.get("photo_media")
                or target.get("cover_media")
            )
            if not media:
                return

            # Check if already settled
            if self.is_media_settled(media):
                return

            # Only describe if media is active (has S3 URL)
            if not self.media_is_active(media):
                return

            s3_url = media.get("s3_url")
            if not s3_url:
                return

            # Get media_id from database - will be fetched later in async context
            items_to_describe.append(
                {
                    "url": s3_url,
                    "context": context_label,
                    "owner_type": owner_type,
                    "owner_id": owner_id,
                    "field_name": field_name,
                }
            )

        # Collect from conversation data
        # Support both "page" and "page_info" keys (different services use different keys)
        page_info = fb_data.get("page_info") or fb_data.get("page") or {}
        page_id = page_info.get("id") or fb_data.get("fan_page_id", "")
        if page_info and page_id:
            collect_media(
                page_info,
                "fan_page",
                page_id,
                "avatar",
                "page avatar",
            )
            collect_media(
                page_info,
                "fan_page",
                page_id,
                "cover",
                "page cover",
            )

        user_info = fb_data.get("user_info") or {}
        if user_info:
            collect_media(
                user_info,
                "page_scope_user",
                user_info.get("id") or fb_data.get("facebook_page_scope_user_id", ""),
                "profile_pic",
                "user avatar",
            )

        # Collect from messages/items
        for item in fb_data.get("items", []):
            collect_media(
                item,
                "message",
                item.get("id", ""),
                "photo_url",
                "message attachment",
            )

        # Collect from comments
        for comment in fb_data.get("comments", []):
            collect_media(
                comment,
                "comment",
                comment.get("id", ""),
                "photo_url",
                "comment attachment",
            )

        # Collect from post
        post_info = fb_data.get("post") or {}
        if post_info:
            collect_media(
                post_info,
                "post",
                post_info.get("id", ""),
                "photo_link",
                "post image",
            )

        # Collect from post_info (if separate from post)
        post_info_separate = fb_data.get("post_info") or {}
        if post_info_separate:
            collect_media(
                post_info_separate,
                "post",
                post_info_separate.get("id", ""),
                "photo_link",
                "post image",
            )

        # Collect from ad_context
        ad_context = fb_data.get("ad_context") or {}
        if ad_context and isinstance(ad_context, dict):
            # Use conversation_id as owner_id for ad_context media
            owner_id = fb_data.get("_conversation_id") or ""
            if not owner_id:
                # Fallback: construct from page_id and user_id
                owner_id = (
                    fb_data.get("fan_page_id", "")
                    + "_"
                    + fb_data.get("facebook_page_scope_user_id", "")
                )
            collect_media(
                ad_context,
                "facebook_conversation",
                owner_id,
                "ad_context_photo_url",
                "ad context image",
            )

        # Batch describe
        if items_to_describe:
            # Get media_ids from database and check if already described
            items_with_ids = []
            for item in items_to_describe:
                db_media = await get_fb_media_asset(
                    conn, item["owner_type"], item["owner_id"], item["field_name"]
                )
                if db_media:
                    # IMPORTANT: Check if already described in DB (prevents duplicate LLM calls)
                    # This handles race conditions where multiple requests process same media
                    if db_media.get("description"):
                        logger.debug(
                            f"Media {db_media.get('id')} already has description, skipping"
                        )
                        # Still update in-memory data with existing description
                        self._update_media_description_in_data(
                            fb_data, item, db_media.get("description")
                        )
                        continue
                    item["media_id"] = db_media.get("id")
                    items_with_ids.append(item)

            if items_with_ids:
                descriptions = await self.description_service.describe_batch(
                    conn=conn,
                    items=items_with_ids,
                    api_key=user_api_key,
                    user_id=user_id or "",
                    parent_agent_response_id=parent_agent_response_id,
                    conversation_id=conversation_id,
                    branch_id=branch_id,
                )

                # Update database with descriptions
                for item in items_with_ids:
                    media_id = item.get("media_id")
                    if not media_id:
                        continue
                    description = descriptions.get(media_id)
                    if description:
                        await update_media_description_by_id(
                            conn,
                            media_id,
                            description,
                            self.description_service.model,
                        )
                        # Update in-memory data
                        self._update_media_description_in_data(
                            fb_data, item, description
                        )

    def _update_media_description_in_data(
        self, fb_data: Dict[str, Any], item: Dict[str, Any], description: str
    ) -> None:
        """Update description in fb_data structure."""
        owner_type = item["owner_type"]
        owner_id = item["owner_id"]
        field_name = item["field_name"]

        # Find the target in fb_data
        target = None
        if owner_type == "fan_page":
            target = fb_data.get("page_info") or {}
        elif owner_type == "page_scope_user":
            target = fb_data.get("user_info") or {}
        elif owner_type == "post":
            target = fb_data.get("post") or {}
        elif owner_type == "message":
            for msg in fb_data.get("items", []):
                if msg.get("id") == owner_id:
                    target = msg
                    break
        elif owner_type == "comment":
            for comment in fb_data.get("comments", []):
                if comment.get("id") == owner_id:
                    target = comment
                    break

        if target and isinstance(target, dict):
            media_attr = (
                "avatar_media"
                if field_name in ("avatar", "profile_pic")
                else "cover_media" if field_name == "cover" else "photo_media"
            )
            media = target.get(media_attr)
            if isinstance(media, dict):
                media["description"] = description
                media["description_model"] = self.description_service.model


__all__ = ["MediaAssetService"]
