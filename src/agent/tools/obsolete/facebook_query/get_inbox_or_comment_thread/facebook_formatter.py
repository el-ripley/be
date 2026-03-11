from typing import Any, Dict, List, Optional

from src.agent.utils import (
    ensure_dict,
    ensure_list,
    extract_url,
    format_timestamp,
    safe_timestamp,
)
from src.services.facebook.media import MediaAssetService
from src.utils.logger import get_logger

from .multimodal import MultimodalContentBuilder

logger = get_logger()


class FacebookContentFormatter:
    def __init__(self, media_asset_service: MediaAssetService):
        self.media_asset_service = media_asset_service

    def _append_media_unavailable_notice(
        self,
        builder: MultimodalContentBuilder,
        media_info: Optional[Dict[str, Any]],
        context_label: str,
        image_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Append a notice when media is unavailable (AI-specific functionality).
        """
        media_dict = ensure_dict(media_info)
        if not self.media_asset_service.media_has_source(media_dict):
            return None
        if self.media_asset_service.media_is_active(media_dict):
            return None

        status = media_dict.get("status") or "unavailable"
        if status == "failed":
            reason = media_dict.get("error") or "download_failed"
        elif status == "expired":
            reason = "expired"
        else:
            reason = status

        # Keep the notice concise; avoid leaking original source URLs.
        message = f"[Image unavailable] {context_label}: {reason}"
        return builder.add_image_notice(message, image_type=image_type)

    def format_conversation_messages(
        self,
        fb_data: Dict[str, Any],
        conv_id: str,
        is_active_tab: bool = False,
        output_mode: str = "description",
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        total_count: Optional[int] = None,
        has_next_page: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Format conversation messages for AI consumption.

        Args:
            fb_data: Facebook conversation data
            conv_id: Conversation ID
            is_active_tab: Whether this is the active tab
            output_mode: "description" (for Agent tools), "text" (plain text for SuggestResponse), or "humes_images"
            page: Current page number
            page_size: Number of items per page
            total_count: Total number of items
            has_next_page: Whether there is a next page
        """
        try:
            if output_mode == "text":
                fb_content = self._build_conversation_text(
                    fb_data, conv_id, page, page_size, total_count, has_next_page
                )
                if not fb_content:
                    fb_content = f"[Facebook Conversation {conv_id}]: No valid messages"
                return {"fb_content": fb_content, "media_entries": []}
            if output_mode == "description":
                # Build payload with embedded descriptions
                payload = self._build_conversation_payload_with_descriptions(
                    fb_data, conv_id, page, page_size, total_count, has_next_page
                )
                if payload:
                    builder = MultimodalContentBuilder()
                    fb_content = builder._dump_json(payload)
                else:
                    fb_content = f"[Facebook Conversation {conv_id}]: No valid messages"
                return {"fb_content": fb_content, "media_entries": []}
            else:
                # Existing behavior: build with image refs
                builder = MultimodalContentBuilder()
                payload = self._build_facebook_conversation_payload(
                    fb_data,
                    conv_id,
                    builder,
                    page,
                    page_size,
                    total_count,
                    has_next_page,
                )
                if payload:
                    fb_content = builder._dump_json(payload)
                else:
                    fb_content = f"[Facebook Conversation {conv_id}]: No valid messages"
                return {
                    "fb_content": fb_content,
                    "media_entries": builder.get_media_entries(),
                }
        except Exception as exc:
            logger.error("Error formatting conversation messages: %s", exc)
            return None

    def format_conversation_comments(
        self,
        fb_data: Dict[str, Any],
        root_comment_id: str,
        is_active_tab: bool = False,
        output_mode: str = "description",
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        total_count: Optional[int] = None,
        has_next_page: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Format comment thread for AI consumption.

        Args:
            fb_data: Facebook comment thread data
            root_comment_id: Root comment ID
            is_active_tab: Whether this is the active tab
            output_mode: "description" (for Agent tools), "text" (plain text for SuggestResponse), or "humes_images"
            page: Current page number
            page_size: Number of items per page
            total_count: Total number of items
            has_next_page: Whether there is a next page
        """
        try:
            if output_mode == "text":
                fb_content = self._build_comment_thread_text(
                    fb_data,
                    root_comment_id,
                    page,
                    page_size,
                    total_count,
                    has_next_page,
                )
                if not fb_content:
                    fb_content = f"[Facebook Comment Thread {root_comment_id}]: No valid comments"
                return {"fb_content": fb_content, "media_entries": []}
            if output_mode == "description":
                # Build payload with embedded descriptions
                payload = self._build_comment_payload_with_descriptions(
                    fb_data,
                    root_comment_id,
                    page,
                    page_size,
                    total_count,
                    has_next_page,
                )
                if payload:
                    builder = MultimodalContentBuilder()
                    fb_content = builder._dump_json(payload)
                else:
                    fb_content = f"[Facebook Comment Thread {root_comment_id}]: No valid comments"
                return {"fb_content": fb_content, "media_entries": []}
            else:
                # Existing behavior: build with image refs
                builder = MultimodalContentBuilder()
                payload = self._build_facebook_comment_payload(
                    fb_data,
                    root_comment_id,
                    builder,
                    page,
                    page_size,
                    total_count,
                    has_next_page,
                )
                if payload:
                    fb_content = builder._dump_json(payload)
                else:
                    fb_content = f"[Facebook Comment Thread {root_comment_id}]: No valid comments"
                return {
                    "fb_content": fb_content,
                    "media_entries": builder.get_media_entries(),
                }
        except Exception as exc:
            logger.error("Error formatting conversation comments: %s", exc)
            return None

    def _format_message_attachment_line(self, att: Dict[str, Any]) -> str:
        """Format a single attachment. Images: no URL but include media_id; video/audio keep URL."""
        att_type = att.get("type") or "file"
        desc = att.get("description") or ""
        url = att.get("url") or ""
        media_id = att.get("media_id") or ""
        is_image = (att_type or "").lower() in (
            "avatar_image",
            "message_image",
            "post_image",
            "image",
        )
        if is_image:
            media_ref = f", media_id: {media_id}" if media_id else ""
            if desc:
                return f"[Attachment: {att_type} - {desc}{media_ref}]"
            return f"[Attachment: {att_type}{media_ref}]"
        if desc:
            return f"[Attachment: {att_type} - {desc}, url: {url}]"
        return f"[Attachment: {att_type}, url: {url}]"

    @staticmethod
    def _normalize_timestamp_seconds(value: Any) -> int:
        """Convert timestamp to seconds. Handles ms (>= 1e12) by dividing by 1000."""
        try:
            ts = int(value)
        except (TypeError, ValueError):
            return 0
        if ts <= 0:
            return 0
        if ts >= 10**12:
            return ts // 1000
        return ts

    def _message_timestamp_seconds(self, item: Dict[str, Any]) -> int:
        """Get message timestamp in seconds for display/sort. Prefer facebook_timestamp, fallback created_at."""
        ts = item.get("facebook_timestamp") or item.get("created_at") or 0
        return self._normalize_timestamp_seconds(ts)

    def _format_info_media_line(self, label: str, att: Dict[str, Any]) -> str:
        """Format page/user/post media with clear label (no URL for images, include media_id)."""
        desc = att.get("description") or ""
        media_id = att.get("media_id") or ""
        media_ref = f", media_id: {media_id}" if media_id else ""
        if desc:
            return f"{label}: {desc}{media_ref}"
        return f"{label}:{media_ref}"

    def _build_conversation_text(
        self,
        fb_data: Dict[str, Any],
        conv_id: str,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        total_count: Optional[int] = None,
        has_next_page: Optional[bool] = None,
    ) -> Optional[str]:
        """Build plain text representation of conversation messages for LLM consumption."""
        data = ensure_dict(fb_data)
        if data is None:
            logger.error("Invalid conversation data for %s", conv_id)
            return None

        raw_items = ensure_list(data.get("items"))
        valid_items: List[Dict[str, Any]] = []
        for item in raw_items:
            item_dict = ensure_dict(item)
            if item_dict is None:
                continue
            valid_items.append(item_dict)

        if not valid_items:
            return None

        page_info_raw = ensure_dict(data.get("page_info")) or {}
        user_info_raw = ensure_dict(data.get("user_info")) or {}
        page_name = page_info_raw.get("name") or "Unknown Page"
        category = (page_info_raw.get("category") or "").strip()
        user_name = user_info_raw.get("name") or "Unknown User"

        lines: List[str] = [
            "=== Conversation Info ===",
            f"Page: {page_name}" + (f" ({category})" if category else ""),
        ]
        page_avatar_media = ensure_dict(page_info_raw.get("avatar_media"))
        page_avatar_att = self._build_media_attachment(
            page_avatar_media,
            page_info_raw.get("avatar"),
            "avatar_image",
            "Page avatar",
        )
        if page_avatar_att:
            lines.append(self._format_info_media_line("Page avatar", page_avatar_att))
        lines.append(f"User: {user_name}")
        user_avatar_media = ensure_dict(user_info_raw.get("avatar_media"))
        user_avatar_att = self._build_media_attachment(
            user_avatar_media,
            user_info_raw.get("avatar"),
            "avatar_image",
            "User avatar",
        )
        if user_avatar_att:
            lines.append(self._format_info_media_line("User avatar", user_avatar_att))

        ad_context = ensure_dict(data.get("ad_context"))
        if ad_context:
            ad_title = (ad_context.get("ad_title") or "").strip()
            if ad_title:
                lines.append(f'Ad context: User replied to ad "{ad_title}"')
            else:
                lines.append("Ad context: User replied to an ad")
            ad_photo_media = ensure_dict(ad_context.get("photo_media"))
            ad_photo_att = self._build_media_attachment(
                ad_photo_media,
                ad_context.get("photo_url"),
                "ad_image",
                "Ad image",
            )
            if ad_photo_att:
                lines.append(self._format_info_media_line("Ad image", ad_photo_att))

        lines.extend(["", "=== Messages ==="])

        sorted_items = sorted(
            valid_items, key=lambda x: self._message_timestamp_seconds(x)
        )
        for item in sorted_items:
            ts_sec = self._message_timestamp_seconds(item)
            ts = format_timestamp(ts_sec)
            ts_compact = (ts or "")[:16] if ts else ""
            is_echo = bool(item.get("is_echo", False))
            sender = "Page" if is_echo else "User"
            text_value = item.get("text")
            text = (
                text_value
                if isinstance(text_value, str) and text_value
                else (text_value or "")
            )
            if text:
                lines.append(f"[{ts_compact}] {sender}: {text}")
            else:
                lines.append(f"[{ts_compact}] {sender}:")

            # Attachments: photo (with description), video, audio
            photo_media = ensure_dict(item.get("photo_media"))
            photo_att = self._build_media_attachment(
                photo_media,
                item.get("photo_url"),
                "message_image",
                "Message image",
            )
            if photo_att:
                lines.append(self._format_message_attachment_line(photo_att))
            video_url = extract_url(item.get("video_url"))
            if video_url:
                lines.append(f"[Attachment: video, url: {video_url}]")
            audio_url = extract_url(item.get("audio_url"))
            if audio_url:
                lines.append(f"[Attachment: audio, url: {audio_url}]")
            lines.append("")

        return "\n".join(lines).rstrip()

    def _build_comment_thread_text(
        self,
        fb_data: Dict[str, Any],
        root_comment_id: str,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        total_count: Optional[int] = None,
        has_next_page: Optional[bool] = None,
    ) -> Optional[str]:
        """Build plain text representation of comment thread for LLM consumption."""
        data = ensure_dict(fb_data)
        if data is None:
            logger.error("Invalid comment thread data for %s", root_comment_id)
            return None

        raw_comments = ensure_list(data.get("comments"))
        valid_comments: List[Dict[str, Any]] = []
        for comment in raw_comments:
            comment_dict = ensure_dict(comment)
            if comment_dict is None:
                continue
            valid_comments.append(comment_dict)

        if not valid_comments:
            return None

        page_info_raw = ensure_dict(data.get("page") or data.get("page_info")) or {}
        post_info_raw = ensure_dict(data.get("post") or data.get("post_info")) or {}
        page_name = page_info_raw.get("name") or "Unknown Page"
        category = (page_info_raw.get("category") or "").strip()
        post_message = (post_info_raw.get("message") or "").strip()
        post_ts = format_timestamp(post_info_raw.get("facebook_created_time")) or ""

        lines: List[str] = [
            "=== Comment Thread Info ===",
            f"Page: {page_name}" + (f" ({category})" if category else ""),
        ]
        page_avatar_media = ensure_dict(page_info_raw.get("avatar_media"))
        page_avatar_att = self._build_media_attachment(
            page_avatar_media,
            page_info_raw.get("avatar"),
            "avatar_image",
            "Page avatar",
        )
        if page_avatar_att:
            lines.append(self._format_info_media_line("Page avatar", page_avatar_att))
        lines.append(f'Post: "{post_message}" ({post_ts})')
        post_photo_media = ensure_dict(post_info_raw.get("photo_media"))
        post_photo_link = post_info_raw.get("photo_link") or post_info_raw.get(
            "full_picture"
        )
        post_photo_att = self._build_media_attachment(
            post_photo_media,
            post_photo_link,
            "post_image",
            "Post image",
        )
        if post_photo_att:
            lines.append(self._format_info_media_line("Post image", post_photo_att))
        post_video_url = extract_url(post_info_raw.get("video_link"))
        if post_video_url:
            lines.append(f"Post video: url: {post_video_url}")
        lines.extend(["", "=== Comments ==="])

        # Compute depth for each comment (0 = root, 1 = reply, etc.)
        comment_by_id: Dict[str, Dict[str, Any]] = {
            str(c.get("id", "")): c for c in valid_comments
        }

        def depth(c: Dict[str, Any]) -> int:
            pid = c.get("parent_comment_id")
            if pid is None or not str(pid):
                return 0
            parent = comment_by_id.get(str(pid))
            if not parent:
                return 0
            return 1 + depth(parent)

        def _comment_ts_sec(c: Dict[str, Any]) -> int:
            return self._normalize_timestamp_seconds(
                c.get("facebook_created_time") or c.get("created_at") or 0
            )

        sorted_comments = sorted(
            valid_comments,
            key=lambda c: (depth(c), _comment_ts_sec(c)),
        )

        for comment in sorted_comments:
            d = depth(comment)
            indent = "  " * d
            ts_sec = _comment_ts_sec(comment)
            ts = format_timestamp(ts_sec)
            ts_compact = (ts or "")[:16] if ts else ""
            author_is_page = comment.get("is_from_page", False)
            author_name = (
                comment.get("fpsu_name") or comment.get("author_name") or "Unknown"
            )
            role_label = "Page" if author_is_page else f"User ({author_name})"
            comment_id = comment.get("id") or ""
            parent_id = comment.get("parent_comment_id")
            msg = (comment.get("message") or "").strip()
            if parent_id:
                line_prefix = (
                    f"{indent}[{ts_compact}] {role_label} (reply to {parent_id}):"
                )
            else:
                line_prefix = (
                    f"{indent}[{ts_compact}] {role_label} (comment: {comment_id}):"
                )
            if msg:
                lines.append(f"{line_prefix} {msg}")
            else:
                lines.append(line_prefix)

            comment_photo_media = ensure_dict(comment.get("photo_media"))
            photo_att = self._build_media_attachment(
                comment_photo_media,
                comment.get("photo_url"),
                "message_image",
                "Comment image",
            )
            if photo_att:
                lines.append(
                    f"{indent}{self._format_message_attachment_line(photo_att)}"
                )
            video_url = extract_url(comment.get("video_url"))
            if video_url:
                lines.append(f"{indent}[Attachment: video, url: {video_url}]")
            lines.append("")

        return "\n".join(lines).rstrip()

    def _build_facebook_conversation_payload(
        self,
        fb_data: Dict[str, Any],
        conv_id: str,
        builder: MultimodalContentBuilder,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        total_count: Optional[int] = None,
        has_next_page: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        data = ensure_dict(fb_data)
        if data is None:
            logger.error("Invalid conversation data for %s", conv_id)
            return None

        raw_items = ensure_list(data.get("items"))
        valid_items: List[Dict[str, Any]] = []
        for item in raw_items:
            item_dict = ensure_dict(item)
            if item_dict is None:
                logger.warning("Skipping malformed conversation item for %s", conv_id)
                continue
            valid_items.append(item_dict)

        if not valid_items:
            return None

        page_info_raw = ensure_dict(data.get("page_info")) or {}
        user_info_raw = ensure_dict(data.get("user_info")) or {}

        payload: Dict[str, Any] = {
            "type": "facebook_conversation",
            "id": conv_id,
            "page_info": {
                "id": page_info_raw.get("id") or data.get("fan_page_id"),
                "name": page_info_raw.get("name") or "Unknown Page",
                "avatar": page_info_raw.get("avatar"),
                "avatar_media": page_info_raw.get("avatar_media"),
                "category": page_info_raw.get("category"),
                "fan_count": page_info_raw.get("fan_count"),
                "followers_count": page_info_raw.get("followers_count"),
                "rating_count": page_info_raw.get("rating_count"),
                "overall_star_rating": (
                    float(page_info_raw.get("overall_star_rating"))
                    if page_info_raw.get("overall_star_rating") is not None
                    else None
                ),
                "about": page_info_raw.get("about"),
                "description": page_info_raw.get("description"),
                "link": page_info_raw.get("link"),
                "website": page_info_raw.get("website"),
                "phone": page_info_raw.get("phone"),
                "emails": page_info_raw.get("emails"),
                "location": page_info_raw.get("location"),
                "cover": page_info_raw.get("cover"),
                "hours": page_info_raw.get("hours"),
                "is_verified": page_info_raw.get("is_verified"),
            },
            "user_info": {
                "id": user_info_raw.get("id")
                or data.get("facebook_page_scope_user_id"),
                "name": user_info_raw.get("name") or "Unknown User",
            },
            "messages": [],
        }

        page_avatar_media = ensure_dict(page_info_raw.get("avatar_media"))
        page_avatar_ref = None
        if self.media_asset_service.media_is_active(page_avatar_media):
            page_avatar_ref = builder.register_image(
                page_avatar_media.get("s3_url"), "avatar_image"
            )
        if page_avatar_ref:
            payload["page_info"]["avatar"] = page_avatar_ref
        else:
            notice_ref = self._append_media_unavailable_notice(
                builder,
                page_avatar_media or {"original_url": page_info_raw.get("avatar")},
                "Page avatar",
                image_type="avatar_image",
            )
            if notice_ref:
                payload["page_info"]["avatar"] = notice_ref

        user_avatar_media = ensure_dict(user_info_raw.get("avatar_media"))
        user_avatar_ref = None
        if self.media_asset_service.media_is_active(user_avatar_media):
            user_avatar_ref = builder.register_image(
                user_avatar_media.get("s3_url"), "avatar_image"
            )
        if user_avatar_ref:
            payload["user_info"]["avatar"] = user_avatar_ref
        else:
            notice_ref = self._append_media_unavailable_notice(
                builder,
                user_avatar_media or {"original_url": user_info_raw.get("avatar")},
                "User avatar",
                image_type="avatar_image",
            )
            if notice_ref:
                payload["user_info"]["avatar"] = notice_ref

        sorted_items = sorted(
            valid_items, key=lambda x: safe_timestamp(x.get("created_at"))
        )

        for item in sorted_items:
            text_value = item.get("text")
            message_entry = {
                "sender": "page_admin" if item.get("is_echo", False) else "page_user",
                "timestamp": format_timestamp(item.get("created_at")),
                "text": (
                    text_value if isinstance(text_value, str) else (text_value or "")
                ),
                "attachments": [],
            }

            attachments: List[Dict[str, Any]] = []

            photo_media = ensure_dict(item.get("photo_media"))
            photo_ref = None
            if self.media_asset_service.media_is_active(photo_media):
                photo_ref = builder.register_image(
                    photo_media.get("s3_url"), "message_image"
                )
            if photo_ref:
                attachments.append(photo_ref)
            else:
                notice_ref = self._append_media_unavailable_notice(
                    builder,
                    photo_media or {"original_url": item.get("photo_url")},
                    "Message image",
                    image_type="message_image",
                )
                if notice_ref:
                    attachments.append(notice_ref)

            video_url = extract_url(item.get("video_url"))
            if video_url:
                attachments.append({"type": "message_video", "url": video_url})

            audio_url = extract_url(item.get("audio_url"))
            if audio_url:
                attachments.append({"type": "message_audio", "url": audio_url})

            # Always set attachments (empty array if no attachments)
            message_entry["attachments"] = attachments
            payload["messages"].append(message_entry)

        # Add pagination info
        if page is not None:
            pagination: Dict[str, Any] = {
                "page": page,
                "page_size": page_size or len(valid_items),
            }
            if total_count is not None:
                pagination["total_count"] = total_count
                pagination["total_pages"] = (
                    (total_count + pagination["page_size"] - 1)
                    // pagination["page_size"]
                    if total_count > 0
                    else 1
                )
            if has_next_page is not None:
                pagination["has_next_page"] = has_next_page
                if has_next_page:
                    pagination["next_page"] = page + 1
            payload["pagination"] = pagination

        return payload

    def _build_facebook_comment_payload(
        self,
        fb_data: Dict[str, Any],
        root_comment_id: str,
        builder: MultimodalContentBuilder,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        total_count: Optional[int] = None,
        has_next_page: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        data = ensure_dict(fb_data)
        if data is None:
            logger.error("Invalid comment thread data for %s", root_comment_id)
            return None

        raw_comments = ensure_list(data.get("comments"))
        valid_comments: List[Dict[str, Any]] = []
        for comment in raw_comments:
            comment_dict = ensure_dict(comment)
            if comment_dict is None:
                logger.warning(
                    "Skipping malformed comment in thread %s", root_comment_id
                )
                continue
            valid_comments.append(comment_dict)

        if not valid_comments:
            return None

        page_info_raw = ensure_dict(data.get("page") or data.get("page_info")) or {}
        post_info_raw = ensure_dict(data.get("post") or data.get("post_info")) or {}

        payload: Dict[str, Any] = {
            "type": "facebook_comment_thread",
            "root_comment_id": root_comment_id,
            "page_info": {
                "id": page_info_raw.get("id"),
                "name": page_info_raw.get("name") or "Unknown Page",
                "avatar": page_info_raw.get("avatar"),
                "category": page_info_raw.get("category"),
                "fan_count": page_info_raw.get("fan_count"),
                "followers_count": page_info_raw.get("followers_count"),
                "rating_count": page_info_raw.get("rating_count"),
                "overall_star_rating": (
                    float(page_info_raw.get("overall_star_rating"))
                    if page_info_raw.get("overall_star_rating") is not None
                    else None
                ),
                "about": page_info_raw.get("about"),
                "description": page_info_raw.get("description"),
                "link": page_info_raw.get("link"),
                "website": page_info_raw.get("website"),
                "phone": page_info_raw.get("phone"),
                "emails": page_info_raw.get("emails"),
                "location": page_info_raw.get("location"),
                "cover": page_info_raw.get("cover"),
                "hours": page_info_raw.get("hours"),
                "is_verified": page_info_raw.get("is_verified"),
            },
            "post_info": {
                "id": post_info_raw.get("id"),
                "message": post_info_raw.get("message") or "",
                "facebook_created_time": format_timestamp(
                    post_info_raw.get("facebook_created_time")
                ),
            },
            "comments": [],
        }

        # Helper caches to reuse avatar refs across participants and comments.
        participant_avatar_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        participants: List[Dict[str, Any]] = []

        def _build_avatar_reference(
            *,
            media_info: Optional[Dict[str, Any]],
            fallback_label: str,
            fallback_url: Optional[str] = None,
        ) -> Optional[Dict[str, Any]]:
            """Try to register avatar image; if missing, append an unavailable notice."""
            ref = self._register_image_reference(
                builder, media_info=media_info, image_type="avatar_image"
            )
            if ref:
                return ref

            # Allow graceful fallback with a media-unavailable notice.
            if media_info or fallback_url:
                notice_ref = self._append_media_unavailable_notice(
                    builder,
                    media_info or {"original_url": fallback_url},
                    fallback_label,
                    image_type="avatar_image",
                )
                if notice_ref:
                    return notice_ref
            return None

        def _participant_key(role: str, pid: Optional[str], name: Optional[str]) -> str:
            return f"{role}:{pid or name or 'unknown'}"

        def _ensure_participant(
            *,
            role: str,
            pid: Optional[str],
            name: Optional[str],
            avatar_media: Optional[Dict[str, Any]],
            fallback_url: Optional[str] = None,
        ) -> Optional[Dict[str, Any]]:
            """
            Add participant once, cache avatar ref for reuse.
            Returns the avatar ref for the participant (if any).
            """
            key = _participant_key(role, pid, name)
            if key in participant_avatar_cache:
                return participant_avatar_cache[key]

            avatar_ref = _build_avatar_reference(
                media_info=avatar_media,
                fallback_label=f"{role} avatar",
                fallback_url=fallback_url,
            )
            participant_avatar_cache[key] = avatar_ref

            participant_entry: Dict[str, Any] = {
                "role": role,
                "id": pid,
                "name": name or "Unknown",
            }
            if avatar_ref:
                participant_entry["avatar"] = avatar_ref
            participants.append(participant_entry)
            return avatar_ref

        page_avatar_media = ensure_dict(page_info_raw.get("avatar_media"))
        page_avatar_ref = self._register_image_reference(
            builder,
            media_info=page_avatar_media,
            image_type="avatar_image",
        )

        if page_avatar_ref:
            payload["page_info"]["avatar"] = page_avatar_ref
        elif page_avatar_media or page_info_raw.get("avatar"):
            notice_ref = self._append_media_unavailable_notice(
                builder,
                page_avatar_media or {"original_url": page_info_raw.get("avatar")},
                "Page avatar",
                image_type="avatar_image",
            )
            if notice_ref:
                payload["page_info"]["avatar"] = notice_ref

        _ensure_participant(
            role="page_admin",
            pid=payload["page_info"].get("id"),
            name=payload["page_info"].get("name"),
            avatar_media=page_avatar_media,
            fallback_url=page_info_raw.get("avatar"),
        )

        post_photo_media = ensure_dict(post_info_raw.get("photo_media"))
        post_photo_ref = self._register_image_reference(
            builder,
            media_info=post_photo_media,
            image_type="post_image",
        )
        if post_photo_ref:
            payload["post_info"]["photo_link"] = post_photo_ref
        elif post_photo_media or post_info_raw.get("photo_link"):
            notice_ref = self._append_media_unavailable_notice(
                builder,
                post_photo_media or {"original_url": post_info_raw.get("photo_link")},
                "Post image",
                image_type="post_image",
            )
            if notice_ref:
                payload["post_info"]["photo_link"] = notice_ref

        post_video_url = extract_url(post_info_raw.get("video_link"))
        if post_video_url:
            payload["post_info"]["video_link"] = post_video_url

        sorted_comments = sorted(
            valid_comments, key=lambda x: safe_timestamp(x.get("created_at"))
        )

        for comment in sorted_comments:
            author_is_page = comment.get("is_from_page", False)
            attachments: List[Dict[str, Any]] = []

            comment_photo_media = ensure_dict(comment.get("photo_media"))
            photo_ref = None
            if self.media_asset_service.media_has_source(comment_photo_media):
                photo_ref = self._register_image_reference(
                    builder,
                    media_info=comment_photo_media,
                    image_type="message_image",
                )
                if photo_ref:
                    attachments.append(photo_ref)
                else:
                    notice_ref = self._append_media_unavailable_notice(
                        builder,
                        comment_photo_media,
                        "Comment image",
                        image_type="message_image",
                    )
                    if notice_ref:
                        attachments.append(notice_ref)

            video_url = extract_url(comment.get("video_url"))
            if video_url:
                attachments.append({"type": "message_video", "url": video_url})

            author_id = (
                payload["page_info"].get("id")
                if author_is_page
                else comment.get("fpsu_id") or comment.get("author_id")
            )
            author_name = (
                payload["page_info"].get("name")
                if author_is_page
                else comment.get("fpsu_name") or comment.get("author_name") or "Unknown"
            )
            author_avatar_media = (
                page_avatar_media
                if author_is_page
                else ensure_dict(comment.get("fpsu_avatar_media"))
            )
            author_avatar_fallback = (
                page_info_raw.get("avatar")
                if author_is_page
                else comment.get("fpsu_profile_pic") or comment.get("author_avatar")
            )

            # Ensure participant is added to participants list
            _ensure_participant(
                role="page_admin" if author_is_page else "page_user",
                pid=author_id,
                name=author_name,
                avatar_media=author_avatar_media,
                fallback_url=author_avatar_fallback,
            )

            entry = {
                "comment_id": comment.get("id"),
                "parent_comment_id": comment.get("parent_comment_id"),
                "author": "page_admin" if author_is_page else "page_user",
                "author_id": author_id,
                "message": comment.get("message", "") or "",
                "admin_has_read": comment.get("mark_as_read")
                or comment.get("admin_has_read", False),
                "is_hidden": comment.get("is_hidden", False),
                "timestamp": format_timestamp(
                    comment.get("created_at") or comment.get("facebook_created_time")
                ),
            }

            # Only add attachments field if not empty
            if attachments:
                entry["attachments"] = attachments

            payload["comments"].append(entry)

        if participants:
            payload["participants"] = participants

        # Add pagination info
        if page is not None:
            pagination: Dict[str, Any] = {
                "page": page,
                "page_size": page_size or len(valid_comments),
            }
            if total_count is not None:
                pagination["total_count"] = total_count
                pagination["total_pages"] = (
                    (total_count + pagination["page_size"] - 1)
                    // pagination["page_size"]
                    if total_count > 0
                    else 1
                )
            if has_next_page is not None:
                pagination["has_next_page"] = has_next_page
                if has_next_page:
                    pagination["next_page"] = page + 1
            payload["pagination"] = pagination

        return payload

    def _build_conversation_payload_with_descriptions(
        self,
        fb_data: Dict[str, Any],
        conv_id: str,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        total_count: Optional[int] = None,
        has_next_page: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Build conversation payload with embedded media descriptions."""
        data = ensure_dict(fb_data)
        if data is None:
            logger.error("Invalid conversation data for %s", conv_id)
            return None

        raw_items = ensure_list(data.get("items"))
        valid_items: List[Dict[str, Any]] = []
        for item in raw_items:
            item_dict = ensure_dict(item)
            if item_dict is None:
                logger.warning("Skipping malformed conversation item for %s", conv_id)
                continue
            valid_items.append(item_dict)

        if not valid_items:
            return None

        page_info_raw = ensure_dict(data.get("page_info")) or {}
        user_info_raw = ensure_dict(data.get("user_info")) or {}

        payload: Dict[str, Any] = {
            "type": "facebook_conversation",
            "id": conv_id,
            "page_info": {
                "id": page_info_raw.get("id") or data.get("fan_page_id"),
                "name": page_info_raw.get("name") or "Unknown Page",
                "avatar": page_info_raw.get("avatar"),
                "category": page_info_raw.get("category"),
                "fan_count": page_info_raw.get("fan_count"),
                "followers_count": page_info_raw.get("followers_count"),
                "rating_count": page_info_raw.get("rating_count"),
                "overall_star_rating": (
                    float(page_info_raw.get("overall_star_rating"))
                    if page_info_raw.get("overall_star_rating") is not None
                    else None
                ),
                "about": page_info_raw.get("about"),
                "description": page_info_raw.get("description"),
                "link": page_info_raw.get("link"),
                "website": page_info_raw.get("website"),
                "phone": page_info_raw.get("phone"),
                "emails": page_info_raw.get("emails"),
                "location": page_info_raw.get("location"),
                "hours": page_info_raw.get("hours"),
                "is_verified": page_info_raw.get("is_verified"),
            },
            "user_info": {
                "id": user_info_raw.get("id")
                or data.get("facebook_page_scope_user_id"),
                "name": user_info_raw.get("name") or "Unknown User",
            },
        }

        # Add ad_context if available (user replied to a Facebook ad)
        ad_context = ensure_dict(data.get("ad_context"))
        if ad_context:
            # Build ad_context with photo_url as media attachment (with description)
            ad_context_payload: Dict[str, Any] = {
                "type": ad_context.get("type"),
                "ad_id": ad_context.get("ad_id"),
                "source": ad_context.get("source"),
                "post_id": ad_context.get("post_id"),
                "ad_title": ad_context.get("ad_title"),
                "product_id": ad_context.get("product_id"),
            }

            # Add photo_url as media attachment with description
            ad_photo_media = ensure_dict(ad_context.get("photo_media"))
            ad_photo_attachment = self._build_media_attachment(
                ad_photo_media,
                ad_context.get("photo_url"),
                "ad_image",
                "Ad image",
            )
            if ad_photo_attachment:
                ad_context_payload["photo"] = ad_photo_attachment
            elif ad_context.get("photo_url"):
                # Fallback: include URL if no media attachment
                ad_context_payload["photo_url"] = ad_context.get("photo_url")

            payload["ad_context"] = ad_context_payload

        # Add post_info if available (when ad_context contains post_id)
        post_info_raw = ensure_dict(data.get("post_info"))
        if post_info_raw:
            post_info: Dict[str, Any] = {
                "id": post_info_raw.get("id"),
                "message": post_info_raw.get("message") or "",
                "facebook_created_time": format_timestamp(
                    post_info_raw.get("facebook_created_time")
                ),
            }

            # Add post photo with description
            post_photo_media = ensure_dict(post_info_raw.get("photo_media"))
            post_photo_attachment = self._build_media_attachment(
                post_photo_media,
                post_info_raw.get("photo_link"),
                "post_image",
                "Post image",
            )
            if post_photo_attachment:
                post_info["photo"] = post_photo_attachment

            payload["post_info"] = post_info

        # Add page avatar with description
        page_avatar_media = ensure_dict(page_info_raw.get("avatar_media"))
        page_avatar_attachment = self._build_media_attachment(
            page_avatar_media,
            page_info_raw.get("avatar"),
            "avatar_image",
            "Page avatar",
        )
        if page_avatar_attachment:
            payload["page_info"]["avatar"] = page_avatar_attachment

        # Add user avatar with description
        user_avatar_media = ensure_dict(user_info_raw.get("avatar_media"))
        user_avatar_attachment = self._build_media_attachment(
            user_avatar_media,
            user_info_raw.get("avatar"),
            "avatar_image",
            "User avatar",
        )
        if user_avatar_attachment:
            payload["user_info"]["avatar"] = user_avatar_attachment

        # Add page cover with description
        page_cover_media = ensure_dict(page_info_raw.get("cover_media"))
        page_cover_attachment = self._build_media_attachment(
            page_cover_media,
            page_info_raw.get("cover"),
            "cover_image",
            "Page cover",
        )
        if page_cover_attachment:
            payload["page_info"]["cover"] = page_cover_attachment

        # Add messages list after ad_context and post_info (for proper ordering)
        payload["messages"] = []

        sorted_items = sorted(
            valid_items, key=lambda x: safe_timestamp(x.get("created_at"))
        )

        for item in sorted_items:
            text_value = item.get("text")
            message_entry = {
                "sender": "page_admin" if item.get("is_echo", False) else "page_user",
                "timestamp": format_timestamp(item.get("created_at")),
                "text": (
                    text_value if isinstance(text_value, str) else (text_value or "")
                ),
                "attachments": [],
            }

            attachments: List[Dict[str, Any]] = []

            # Add photo with description
            photo_media = ensure_dict(item.get("photo_media"))
            photo_attachment = self._build_media_attachment(
                photo_media,
                item.get("photo_url"),
                "message_image",
                "Message image",
            )
            if photo_attachment:
                attachments.append(photo_attachment)

            # Add video URL (no description needed)
            video_url = extract_url(item.get("video_url"))
            if video_url:
                attachments.append({"type": "message_video", "url": video_url})

            # Add audio URL (no description needed)
            audio_url = extract_url(item.get("audio_url"))
            if audio_url:
                attachments.append({"type": "message_audio", "url": audio_url})

            # Always set attachments (empty array if no attachments)
            message_entry["attachments"] = attachments
            payload["messages"].append(message_entry)

        # Add pagination info
        if page is not None:
            pagination: Dict[str, Any] = {
                "page": page,
                "page_size": page_size or len(valid_items),
            }
            if total_count is not None:
                pagination["total_count"] = total_count
                pagination["total_pages"] = (
                    (total_count + pagination["page_size"] - 1)
                    // pagination["page_size"]
                    if total_count > 0
                    else 1
                )
            if has_next_page is not None:
                pagination["has_next_page"] = has_next_page
                if has_next_page:
                    pagination["next_page"] = page + 1
            payload["pagination"] = pagination

        return payload

    def _build_comment_payload_with_descriptions(
        self,
        fb_data: Dict[str, Any],
        root_comment_id: str,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        total_count: Optional[int] = None,
        has_next_page: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Build comment thread payload with embedded media descriptions."""
        data = ensure_dict(fb_data)
        if data is None:
            logger.error("Invalid comment thread data for %s", root_comment_id)
            return None

        raw_comments = ensure_list(data.get("comments"))
        valid_comments: List[Dict[str, Any]] = []
        for comment in raw_comments:
            comment_dict = ensure_dict(comment)
            if comment_dict is None:
                logger.warning(
                    "Skipping malformed comment in thread %s", root_comment_id
                )
                continue
            valid_comments.append(comment_dict)

        if not valid_comments:
            return None

        page_info_raw = ensure_dict(data.get("page") or data.get("page_info")) or {}
        post_info_raw = ensure_dict(data.get("post") or data.get("post_info")) or {}

        payload: Dict[str, Any] = {
            "type": "facebook_comment_thread",
            "root_comment_id": root_comment_id,
            "page_info": {
                "id": page_info_raw.get("id"),
                "name": page_info_raw.get("name") or "Unknown Page",
                "avatar": page_info_raw.get("avatar"),
                "category": page_info_raw.get("category"),
                "fan_count": page_info_raw.get("fan_count"),
                "followers_count": page_info_raw.get("followers_count"),
                "rating_count": page_info_raw.get("rating_count"),
                "overall_star_rating": (
                    float(page_info_raw.get("overall_star_rating"))
                    if page_info_raw.get("overall_star_rating") is not None
                    else None
                ),
                "about": page_info_raw.get("about"),
                "description": page_info_raw.get("description"),
                "link": page_info_raw.get("link"),
                "website": page_info_raw.get("website"),
                "phone": page_info_raw.get("phone"),
                "emails": page_info_raw.get("emails"),
                "location": page_info_raw.get("location"),
                "hours": page_info_raw.get("hours"),
                "is_verified": page_info_raw.get("is_verified"),
            },
            "post_info": {
                "id": post_info_raw.get("id"),
                "message": post_info_raw.get("message") or "",
                "facebook_created_time": format_timestamp(
                    post_info_raw.get("facebook_created_time")
                ),
            },
            "comments": [],
        }

        # Add page avatar
        page_avatar_media = ensure_dict(page_info_raw.get("avatar_media"))
        page_avatar_attachment = self._build_media_attachment(
            page_avatar_media,
            page_info_raw.get("avatar"),
            "avatar_image",
            "Page avatar",
        )
        if page_avatar_attachment:
            payload["page_info"]["avatar"] = page_avatar_attachment

        # Add page cover with description
        page_cover_media = ensure_dict(page_info_raw.get("cover_media"))
        page_cover_attachment = self._build_media_attachment(
            page_cover_media,
            page_info_raw.get("cover"),
            "cover_image",
            "Page cover",
        )
        if page_cover_attachment:
            payload["page_info"]["cover"] = page_cover_attachment

        # Add post photo with description
        # Try to get photo_media from post_info_raw
        post_photo_media = ensure_dict(post_info_raw.get("photo_media"))
        post_photo_link = post_info_raw.get("photo_link")
        # Use full_picture as fallback if photo_link is null
        full_picture = post_info_raw.get("full_picture")
        if not post_photo_link:
            post_photo_link = full_picture

        # Build attachment (will return None if no media source)
        post_photo_attachment = self._build_media_attachment(
            post_photo_media,
            post_photo_link,
            "post_image",
            "Post image",
        )

        # Only add photo field if we actually have photo data
        # Check if post has photo_link (non-empty string) or valid photo_media
        has_photo_link = (
            post_photo_link
            and isinstance(post_photo_link, str)
            and post_photo_link.strip()
        )
        has_photo_media = (
            post_photo_media
            and self.media_asset_service.media_has_source(post_photo_media)
        )

        if post_photo_attachment:
            # Media has been processed and has attachment
            payload["post_info"]["photo"] = post_photo_attachment
        elif has_photo_link or has_photo_media:
            # Media exists but hasn't been processed yet - add placeholder
            # Use original_url from photo_media if available, otherwise use photo_link
            photo_url = None
            if post_photo_media:
                photo_url = post_photo_media.get(
                    "original_url"
                ) or post_photo_media.get("s3_url")
            if not photo_url and has_photo_link:
                photo_url = post_photo_link

            # Only add photo field if we have a valid URL
            if photo_url:
                payload["post_info"]["photo"] = {
                    "type": "post_image",
                    "url": photo_url,
                    "description": (
                        post_photo_media.get("description")
                        if post_photo_media
                        else None
                    ),
                    "media_id": (
                        post_photo_media.get("id") if post_photo_media else None
                    ),
                }

        # Add post video
        post_video_url = extract_url(post_info_raw.get("video_link"))
        if post_video_url:
            payload["post_info"]["video_link"] = post_video_url

        # Build participants list (unique authors)
        participants: List[Dict[str, Any]] = []
        participants_seen: set = set()

        def _add_participant(
            author_id: str,
            author_name: str,
            author_is_page: bool,
            avatar_media: Optional[Dict[str, Any]],
            avatar_fallback: Optional[str],
        ) -> None:
            """Add participant to list if not already present."""
            if not author_id or author_id in participants_seen:
                return

            participants_seen.add(author_id)

            participant: Dict[str, Any] = {
                "id": author_id,
                "name": author_name,
                "role": "page_admin" if author_is_page else "page_user",
            }

            # Add avatar
            avatar_attachment = self._build_media_attachment(
                avatar_media,
                avatar_fallback,
                "avatar_image",
                f"{'Page' if author_is_page else 'User'} avatar",
            )
            if avatar_attachment:
                participant["avatar"] = avatar_attachment

            participants.append(participant)

        # Add page as participant
        page_id = payload["page_info"].get("id")
        if page_id:
            _add_participant(
                page_id,
                payload["page_info"].get("name", "Unknown Page"),
                True,
                page_avatar_media,
                page_info_raw.get("avatar"),
            )

        sorted_comments = sorted(
            valid_comments, key=lambda x: safe_timestamp(x.get("created_at"))
        )

        for comment in sorted_comments:
            author_is_page = comment.get("is_from_page", False)
            attachments: List[Dict[str, Any]] = []

            # Add comment photo with description
            comment_photo_media = ensure_dict(comment.get("photo_media"))
            photo_attachment = self._build_media_attachment(
                comment_photo_media,
                comment.get("photo_url"),
                "message_image",
                "Comment image",
            )
            if photo_attachment:
                attachments.append(photo_attachment)

            # Add video URL
            video_url = extract_url(comment.get("video_url"))
            if video_url:
                attachments.append({"type": "message_video", "url": video_url})

            author_id = (
                payload["page_info"].get("id")
                if author_is_page
                else comment.get("fpsu_id") or comment.get("author_id")
            )
            author_name = (
                payload["page_info"].get("name")
                if author_is_page
                else comment.get("fpsu_name") or comment.get("author_name") or "Unknown"
            )

            # Add author to participants if not already present
            author_avatar_media = (
                page_avatar_media
                if author_is_page
                else ensure_dict(comment.get("fpsu_avatar_media"))
            )
            author_avatar_fallback = (
                page_info_raw.get("avatar")
                if author_is_page
                else comment.get("fpsu_profile_pic") or comment.get("author_avatar")
            )
            _add_participant(
                author_id,
                author_name,
                author_is_page,
                author_avatar_media,
                author_avatar_fallback,
            )

            entry = {
                "comment_id": comment.get("id"),
                "parent_comment_id": comment.get("parent_comment_id"),
                "author": "page_admin" if author_is_page else "page_user",
                "author_id": author_id,
                "message": comment.get("message", "") or "",
                "attachments": attachments,
                "admin_has_read": comment.get("mark_as_read")
                or comment.get("admin_has_read", False),
                "is_hidden": comment.get("is_hidden", False),
                "timestamp": format_timestamp(
                    comment.get("created_at") or comment.get("facebook_created_time")
                ),
            }

            payload["comments"].append(entry)

        # Add participants list to payload
        if participants:
            payload["participants"] = participants

        # Add pagination info
        if page is not None:
            pagination: Dict[str, Any] = {
                "page": page,
                "page_size": page_size or len(valid_comments),
            }
            if total_count is not None:
                pagination["total_count"] = total_count
                pagination["total_pages"] = (
                    (total_count + pagination["page_size"] - 1)
                    // pagination["page_size"]
                    if total_count > 0
                    else 1
                )
            if has_next_page is not None:
                pagination["has_next_page"] = has_next_page
                if has_next_page:
                    pagination["next_page"] = page + 1
            payload["pagination"] = pagination

        return payload

    def _build_media_attachment(
        self,
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
        media_dict = ensure_dict(media_info)

        # Check if we have a source
        has_source = self.media_asset_service.media_has_source(media_dict) or bool(
            fallback_url
        )
        if not has_source:
            return None

        # Check if media is active (has S3 URL)
        is_active = self.media_asset_service.media_is_active(media_dict)

        # Get description
        description = None
        if media_dict:
            description = media_dict.get("description")
            if not description and media_dict.get("status") == "failed":
                error = media_dict.get("error") or "download_failed"
                description = f"[Image unavailable - {error}]"
            elif not description and not is_active:
                description = "[Image unavailable - expired]"

        # Get URL
        url = None
        if is_active:
            url = media_dict.get("s3_url")
        elif fallback_url and not media_dict:
            # No media dict but we have fallback URL (FB URL)
            url = fallback_url

        # Extract media_id if available
        media_id = None
        if media_dict:
            media_id_raw = media_dict.get("id")
            # Convert UUID to string if needed
            if media_id_raw is not None:
                media_id = (
                    str(media_id_raw)
                    if hasattr(media_id_raw, "__str__")
                    else media_id_raw
                )

        return {
            "type": image_type,
            "url": url,
            "description": description,
            "media_id": media_id,
        }

    def _register_image_reference(
        self,
        builder: MultimodalContentBuilder,
        *,
        media_info: Optional[Dict[str, Any]],
        image_type: str,
    ) -> Optional[Dict[str, Any]]:
        media_dict = ensure_dict(media_info)
        if not media_dict:
            return None

        if not self.media_asset_service.media_is_active(media_dict):
            return None

        # Priority: S3 URL first (required for OpenAI to access)
        # Only fallback to non-S3 URLs if explicitly needed
        for key in ("s3_url", "url", "original_url"):
            candidate = extract_url(media_dict.get(key))
            if not candidate:
                continue
            # Skip Facebook CDN URLs - OpenAI cannot access them
            if "fbcdn.net" in candidate or "facebook.com" in candidate:
                continue
            ref = builder.register_image(candidate, image_type)
            if ref:
                return ref
        return None


__all__ = ["FacebookContentFormatter"]
