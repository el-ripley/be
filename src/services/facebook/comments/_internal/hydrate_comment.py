"""
Comment hydration service - enriches comment data from multiple sources.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from src.database.postgres.repositories.facebook_queries import get_comment

from .helpers import get_comment_data
from .models import CommentHydrationPayload


async def hydrate_comment_payload(
    conn,
    *,
    comment_id: str,
    post_id: str,
    page_admins: List[Dict[str, Any]],
    comment_data: Dict[str, Any],
) -> CommentHydrationPayload:
    """
    Hydrate comment payload by fetching additional data from Graph API or database.

    Priority:
    1. Graph API fetch (most accurate, real-time)
    2. Existing database record (fallback)
    3. Webhook payload (last resort)

    Args:
        conn: Database connection
        comment_id: Facebook comment ID
        post_id: Facebook post ID
        page_admins: List of page admins with access tokens
        comment_data: Raw webhook comment data

    Returns:
        CommentHydrationPayload with enriched data
    """
    is_root_comment = comment_data.get("parent_id") == post_id
    from_id = None
    parent_comment_id = None
    message = comment_data.get("message", "")
    photo_url = comment_data.get("photo")
    video_url = comment_data.get("video")

    # Try fetching from Graph API first
    fetched_comment_data = await get_comment_data(
        comment_id=comment_id,
        page_admins=page_admins,
    )

    if fetched_comment_data:
        from_id = fetched_comment_data.get("from", {}).get("id", "")
        parent_comment_id = fetched_comment_data.get("parent", {}).get("id")
        message = fetched_comment_data.get("message", "") or message
        attachment = fetched_comment_data.get("attachment")
        photo_url, video_url = _extract_attachment_urls(
            attachment, photo_url, video_url
        )
    else:
        # Fallback to existing database record
        existing_comment = await get_comment(conn, comment_id)
        if existing_comment:
            from_id = _extract_from_id_from_existing_comment(existing_comment)
            parent_comment_id = existing_comment.get("parent_comment_id")
            message = existing_comment.get("message", message)
            photo_url = existing_comment.get("photo_url")
            video_url = existing_comment.get("video_url")

    return CommentHydrationPayload(
        message=message,
        photo_url=photo_url,
        video_url=video_url,
        from_id=from_id,
        parent_comment_id=parent_comment_id,
        is_root_comment=is_root_comment,
        fetched_comment_data=fetched_comment_data,
    )


def _extract_from_id_from_existing_comment(
    existing_comment: Dict[str, Any],
) -> Optional[str]:
    """
    Extract from_id from an existing comment record.

    Handles both page comments and user comments with JSONB user_info.
    """
    if existing_comment.get("is_from_page"):
        return existing_comment.get("fan_page_id")

    raw_user_info = existing_comment.get("user_info")

    # Handle both dict (from JSONB auto-decode) and string
    if isinstance(raw_user_info, dict):
        user_info = raw_user_info
    elif isinstance(raw_user_info, str):
        try:
            user_info = json.loads(raw_user_info)
        except (json.JSONDecodeError, TypeError):
            user_info = None
    else:
        user_info = None

    return user_info.get("id") if isinstance(user_info, dict) else None


def _extract_attachment_urls(
    attachment: Optional[Dict[str, Any]],
    fallback_photo: Optional[str],
    fallback_video: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract photo and video URLs from Facebook attachment data.

    Args:
        attachment: Facebook attachment object
        fallback_photo: Fallback photo URL if not found in attachment
        fallback_video: Fallback video URL if not found in attachment

    Returns:
        Tuple of (photo_url, video_url)
    """
    if not attachment:
        return fallback_photo, fallback_video

    photo_url = fallback_photo
    video_url = fallback_video
    attachment_type = attachment.get("type")

    if attachment_type == "photo":
        photo_url = attachment.get("media", {}).get("image", {}).get("src") or photo_url
    elif attachment_type == "video":
        video_url = attachment.get("url") or video_url
    elif attachment_type == "share":
        target = attachment.get("target", {}) or {}
        target_url = target.get("url", "")
        if "photo" in target_url:
            photo_url = target_url or photo_url
        else:
            video_url = target_url or video_url

    return photo_url, video_url
