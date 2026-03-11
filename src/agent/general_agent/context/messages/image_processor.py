from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from src.agent.utils import (
    ensure_content_items,
    looks_like_content_list,
    replace_expired_images_with_map,
)
from src.api.openai_conversations.schemas import MessageResponse
from src.utils.logger import get_logger

logger = get_logger()


class ImageProcessor:
    """Utilities for handling images inside message content.

    Responsibilities:
    - Collect all image URLs from a batch of messages
    - Batch-query media assets (single DB round-trip)
    - Apply expiration map to content
    - Attach a system-reminder with image URLs + media_ids for user messages
    """

    @staticmethod
    def collect_image_urls_from_messages(
        branch_messages: List[MessageResponse],
    ) -> List[str]:
        """Collect all image URLs from a list of MessageResponse objects."""
        all_image_urls: List[str] = []
        for msg in branch_messages:
            if msg.is_hidden:
                continue

            content = (
                getattr(msg, "modified_content", None) if msg.is_modified else None
            )
            if content is None:
                content = msg.content

            if looks_like_content_list(content):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "input_image":
                        image_url = item.get("image_url")
                        if image_url:
                            all_image_urls.append(image_url)

        return all_image_urls

    @staticmethod
    async def batch_query_media_assets(
        conn: Optional[asyncpg.Connection],
        all_image_urls: List[str],
    ) -> Tuple[Dict[str, Optional[int]], Dict[str, Optional[str]]]:
        """Batch query media_assets for all image URLs.

        Returns:
            expiration_map: url -> expires_at (epoch ms or None)
            media_id_map: url -> media_id (or None)
        """
        expiration_map: Dict[str, Optional[int]] = {}
        media_id_map: Dict[str, Optional[str]] = {}

        if not conn or not all_image_urls:
            return expiration_map, media_id_map

        try:
            from src.database.postgres.repositories.media_assets_queries import (
                batch_get_media_assets_by_urls,
            )

            media_info = await batch_get_media_assets_by_urls(conn, all_image_urls)
            for url, info in media_info.items():
                expiration_map[url] = info.get("expires_at")
                media_id_map[url] = info.get("id")

            if len(media_info) < len(all_image_urls):
                missing_urls = set(all_image_urls) - set(media_info.keys())
                logger.debug(
                    "Found %s media assets out of %s URLs. Missing URLs: %s...",
                    len(media_info),
                    len(all_image_urls),
                    list(missing_urls)[:3],
                )

                # Mark missing URLs as stale (expires_at=0 means already expired).
                # These are input_image URLs from our system that should be in
                # media_assets. If not found, the media was moved/deleted (e.g.
                # change_media_retention updated s3_url to a new location).
                # Setting expires_at=0 causes replace_expired_images_with_map()
                # to replace them with placeholder text, preventing 403 errors
                # when OpenAI tries to download stale S3 URLs.
                for url in missing_urls:
                    expiration_map[url] = 0
            else:
                logger.debug(
                    "Found %s media assets for %s URLs",
                    len(media_info),
                    len(all_image_urls),
                )
        except Exception as exc:
            logger.warning(
                "Failed to batch query media_assets for expiration check: %s", exc
            )

        return expiration_map, media_id_map

    @staticmethod
    async def process_content_with_images(
        *,
        normalized_role: str,
        content_payload: Any,
        expiration_map: Optional[Dict[str, Optional[int]]] = None,
        media_id_map: Optional[Dict[str, Optional[str]]] = None,
    ) -> Any:
        """Apply image expiration and append image reminder when needed.

        Assumes:
        - `content_payload` has already been normalized at the string level
          (e.g. tool outputs, custom role prefixes).
        - `normalized_role` is one of: user/assistant/system/developer.
        """
        expiration_map = expiration_map or {}
        media_id_map = media_id_map or {}

        if content_payload is None:
            content_payload = ""

        # If content is already a multimodal list, only check expiration
        if looks_like_content_list(content_payload):
            normalized_content = await replace_expired_images_with_map(
                content_payload, expiration_map
            )
        else:
            # Convert string to content items format
            normalized_content = ensure_content_items(content_payload, normalized_role)

        # For user messages, append system-reminder with image URLs and media_ids
        if normalized_role == "user" and isinstance(normalized_content, list):
            image_items: List[Dict[str, Optional[str]]] = []
            for item in normalized_content:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "input_image"
                    and item.get("image_url")
                ):
                    image_url = item.get("image_url")
                    media_id = ImageProcessor._lookup_media_id(
                        image_url=image_url,
                        media_id_map=media_id_map,
                    )
                    image_items.append({"url": image_url, "media_id": media_id})

            if image_items:
                reminder_text = ImageProcessor.build_image_reminder_text(image_items)
                normalized_content = [
                    *normalized_content,
                    {"type": "input_text", "text": reminder_text},
                ]

        return normalized_content

    @staticmethod
    def _lookup_media_id(
        *, image_url: str, media_id_map: Dict[str, Optional[str]]
    ) -> Optional[str]:
        """Best-effort lookup of media_id for a given image URL.

        Implements:
        - direct match
        - normalized URL match (strip trailing slash)
        - filename-based match (UUID in path)
        """
        if not media_id_map:
            return None

        # 1. Exact match
        media_id = media_id_map.get(image_url)
        if media_id:
            return media_id

        # 2. Normalized URL match (strip trailing slash)
        normalized_url = image_url.rstrip("/")
        for map_url, map_id in media_id_map.items():
            if normalized_url == map_url.rstrip("/"):
                logger.debug(
                    "Matched URL after normalization: %s -> %s", image_url, map_url
                )
                return map_id

        # 3. Filename-based match (last path component without query)
        try:
            url_filename = image_url.split("/")[-1].split("?")[0]
            for map_url, map_id in media_id_map.items():
                map_filename = map_url.split("/")[-1].split("?")[0]
                if url_filename == map_filename:
                    logger.debug(
                        "Matched URL by filename: %s -> %s", image_url, map_url
                    )
                    return map_id
        except Exception:
            # Best-effort, ignore parsing errors
            pass

        logger.debug(
            "No media_id found for URL: %s. Available keys: %s...",
            image_url,
            list(media_id_map.keys())[:2],
        )
        return None

    @staticmethod
    def build_image_reminder_text(
        image_items: List[Dict[str, Optional[str]]],
    ) -> str:
        """Build the system-reminder text listing images and media_ids.

        Includes explanation that these images are already attached as input_image
        and visible via vision capabilities, so view_media tool is not needed.
        """
        lines = [
            "Images in this message:",
        ]
        for i, img_item in enumerate(image_items, start=1):
            media_id = img_item.get("media_id") or ""
            lines.append(f'- Image {i}: media_id="{media_id}"')

        lines.append("")
        lines.append(
            "IMPORTANT: These images are already visible to you as input_image attachments. "
            "You can see them directly with your vision capabilities - no additional tools "
            "(view_media or describe_media) needed for viewing them, as they are already loaded in your context."
        )

        return "<system-reminder>\n" + "\n".join(lines) + "\n</system-reminder>"


__all__ = ["ImageProcessor"]
