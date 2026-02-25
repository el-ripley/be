"""Formatter for post details with media."""

from typing import Any, Dict, Optional

from .multimodal import MultimodalContentBuilder


def format_post_details(
    post_data: Dict[str, Any],
    builder: MultimodalContentBuilder,
    output_mode: str = "description",
) -> Optional[str]:
    """
    Format post details into JSON string with media references or descriptions.

    Args:
        post_data: Post data dictionary
        builder: MultimodalContentBuilder instance
        output_mode: "description" (embed descriptions) or "humes_images" (use image refs)

    Returns:
        JSON string with post details and media references/descriptions
    """
    try:
        # Build post payload - keep post_message and post_photo close together
        payload: Dict[str, Any] = {
            "type": "facebook_post",
            "id": post_data.get("id"),
            "post_message": post_data.get("message") or "",
        }

        # Handle media - place post_photo right after post_message
        photo_link = post_data.get("photo_link") or post_data.get("full_picture")
        video_link = post_data.get("video_link")
        photo_media = post_data.get("photo_media")

        if output_mode == "description" and photo_media:
            # Embed description in payload
            photo_attachment = _build_media_attachment(
                photo_media, photo_link, "post_image", "Post image"
            )
            if photo_attachment:
                payload["post_photo"] = photo_attachment
        elif photo_media or photo_link:
            # Use image reference (humes_images mode)
            # Prefer S3 URL from photo_media, fallback to photo_link
            # Skip Facebook CDN URLs - OpenAI cannot access them
            image_url = None
            if photo_media and photo_media.get("status") == "ready":
                image_url = photo_media.get("s3_url")
            if not image_url and photo_link:
                # Only use photo_link if it's not a Facebook CDN URL
                if "fbcdn.net" not in photo_link and "facebook.com" not in photo_link:
                    image_url = photo_link

            if image_url:
                photo_ref = builder.register_image(image_url, "post_image")
                if photo_ref:
                    payload["post_photo"] = photo_ref

        # Add other fields after post_photo
        payload["permalink_url"] = post_data.get("permalink_url")
        payload["status_type"] = post_data.get("status_type")
        payload["is_published"] = post_data.get("is_published", True)
        payload["created_time"] = post_data.get("facebook_created_time")
        payload["reactions"] = {
            "total": post_data.get("reaction_total_count", 0),
            "like": post_data.get("reaction_like_count", 0),
            "love": post_data.get("reaction_love_count", 0),
            "haha": post_data.get("reaction_haha_count", 0),
            "wow": post_data.get("reaction_wow_count", 0),
            "sad": post_data.get("reaction_sad_count", 0),
            "angry": post_data.get("reaction_angry_count", 0),
            "care": post_data.get("reaction_care_count", 0),
        }
        payload["comment_count"] = post_data.get("comment_count", 0)
        payload["share_count"] = post_data.get("share_count", 0)

        if video_link:
            payload["video_link"] = video_link

        # Add top reactors if available
        if post_data.get("top_reactors"):
            payload["top_reactors"] = post_data.get("top_reactors")

        return builder._dump_json(payload)
    except Exception:
        return None


def _build_media_attachment(
    media_info: Optional[Dict[str, Any]],
    fallback_url: Optional[str],
    image_type: str,
    context_label: str,
) -> Optional[Dict[str, Any]]:
    """
    Build media attachment dict with type, url, and description.

    Returns:
        Dict with type, url (S3 URL or None), and description (text or None)
        Returns None if no media source available
    """
    if not media_info and not fallback_url:
        return None

    # Get description from media_info
    description = None
    if media_info:
        description = media_info.get("description")
        if not description and media_info.get("status") == "failed":
            error = media_info.get("error") or "download_failed"
            description = f"[Image unavailable - {error}]"
        elif not description:
            # Check if media is active
            is_active = media_info.get("status") == "ready"
            expires_at = media_info.get("expires_at")
            if expires_at:
                from src.database.postgres.utils import get_current_timestamp_ms

                is_active = is_active and int(expires_at) > get_current_timestamp_ms()
            if not is_active:
                description = "[Image unavailable - expired]"

    # Get URL
    url = None
    if media_info and media_info.get("status") == "ready":
        url = media_info.get("s3_url")
    elif fallback_url:
        url = fallback_url

    # Extract media_id if available
    media_id = None
    if media_info:
        media_id_raw = media_info.get("id")
        # Convert UUID to string if needed
        if media_id_raw is not None:
            media_id = (
                str(media_id_raw) if hasattr(media_id_raw, "__str__") else media_id_raw
            )

    return {
        "type": image_type,
        "url": url,
        "description": description,
        "media_id": media_id,
    }


__all__ = ["format_post_details"]
