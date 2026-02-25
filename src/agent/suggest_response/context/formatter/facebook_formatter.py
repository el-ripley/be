"""
Facebook content formatter for Suggest Response agent context only.
Outputs plain text for LLM consumption (messages and comment threads).
"""

from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger
from src.services.facebook.media import MediaAssetService
from src.agent.utils import (
    ensure_dict,
    ensure_list,
    extract_url,
    format_timestamp,
)

logger = get_logger()


class FacebookContentFormatter:
    """Format Facebook conversation (messages or comments) as plain text for suggest_response context."""

    def __init__(self, media_asset_service: MediaAssetService):
        self.media_asset_service = media_asset_service

    def format_conversation_messages(
        self,
        fb_data: Dict[str, Any],
        conv_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Format conversation messages as plain text for suggest_response context."""
        try:
            fb_content = self._build_conversation_text(fb_data, conv_id)
            if not fb_content:
                fb_content = f"[Facebook Conversation {conv_id}]: No valid messages"
            return {"fb_content": fb_content, "media_entries": []}
        except Exception as exc:
            logger.error("Error formatting conversation messages: %s", exc)
            return None

    def format_comment_thread_identity(self, fb_data: Dict[str, Any]) -> str:
        """Build identity lines for comments system prompt (page name, post summary, page avatar)."""
        data = ensure_dict(fb_data)
        if not data:
            return ""
        page_info_raw = ensure_dict(data.get("page") or data.get("page_info")) or {}
        post_info_raw = ensure_dict(data.get("post") or data.get("post_info")) or {}
        page_name = page_info_raw.get("name") or "Unknown Page"
        category = (page_info_raw.get("category") or "").strip()
        post_message = (post_info_raw.get("message") or "").strip()
        post_ts = format_timestamp(post_info_raw.get("facebook_created_time")) or ""
        page_label = f"**{page_name}**" + (f" ({category})" if category else "")
        lines: List[str] = [
            f"You represent {page_label} on this Facebook post.",
            f'Post: "{post_message}" ({post_ts})',
        ]
        page_avatar_media = ensure_dict(page_info_raw.get("avatar_media"))
        page_avatar_att = self._build_media_attachment(
            page_avatar_media,
            page_info_raw.get("avatar"),
            "avatar_image",
            "Page avatar",
        )
        if page_avatar_att:
            lines.append(self._format_image_tag("page_avatar", page_avatar_att))
        return "\n".join(lines)

    def format_conversation_comments(
        self,
        fb_data: Dict[str, Any],
        root_comment_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Format comment thread as plain text for suggest_response context."""
        try:
            fb_content = self._build_comment_thread_text(fb_data, root_comment_id)
            if not fb_content:
                fb_content = (
                    f"[Facebook Comment Thread {root_comment_id}]: No valid comments"
                )
            return {"fb_content": fb_content, "media_entries": []}
        except Exception as exc:
            logger.error("Error formatting conversation comments: %s", exc)
            return None

    def format_messages_as_turns(
        self,
        fb_data: Dict[str, Any],
        conv_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return structured data for building real user/assistant turns.

        Returns:
            {
                "conversation_info": str,   # Page name, user name, avatars (no ad context)
                "ad_context": str,          # Ad context lines (separate from conversation_info)
                "turns": [                  # Chronological, consecutive same-role merged
                    {"role": "user", "content_parts": ["[ts] #1 msg1", "[ts] #2 msg2\n[Attachment: ...]"]},
                    {"role": "assistant", "content_parts": ["[ts] #3 reply"]},
                    ...
                ],
                "message_ref_map": {"#1": "m_xxx", "#2": "m_yyy", ...}
            }
            or None on error.

        Each element in content_parts is one original message (text + its attachments).
        The context_builder converts each part into a separate content block.
        Messages are tagged with sequential #N indexes for reply threading.
        """
        try:
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

            # Build conversation_info (reuse same logic as _build_conversation_text)
            page_info_raw = ensure_dict(data.get("page_info")) or {}
            user_info_raw = ensure_dict(data.get("user_info")) or {}
            page_name = page_info_raw.get("name") or "Unknown Page"
            category = (page_info_raw.get("category") or "").strip()
            user_name = user_info_raw.get("name") or "Unknown User"

            page_label = f"**{page_name}**" + (f" ({category})" if category else "")
            info_lines: List[str] = [
                f"You represent {page_label} on Facebook Messenger.",
                f"Customer: **{user_name}**",
            ]
            page_avatar_media = ensure_dict(page_info_raw.get("avatar_media"))
            page_avatar_att = self._build_media_attachment(
                page_avatar_media,
                page_info_raw.get("avatar"),
                "avatar_image",
                "Page avatar",
            )
            if page_avatar_att:
                info_lines.append(
                    self._format_image_tag("page_avatar", page_avatar_att)
                )
            user_avatar_media = ensure_dict(user_info_raw.get("avatar_media"))
            user_avatar_att = self._build_media_attachment(
                user_avatar_media,
                user_info_raw.get("avatar"),
                "avatar_image",
                "User avatar",
            )
            if user_avatar_att:
                info_lines.append(
                    self._format_image_tag("user_avatar", user_avatar_att)
                )

            ad_context = ensure_dict(data.get("ad_context"))
            ad_context_lines: List[str] = []
            if ad_context:
                ad_title = (ad_context.get("ad_title") or "").strip()
                if ad_title:
                    ad_context_lines.append(f'Ad context: User replied to ad "{ad_title}"')
                else:
                    ad_context_lines.append("Ad context: User replied to an ad")
                ad_photo_media = ensure_dict(ad_context.get("photo_media"))
                ad_photo_att = self._build_media_attachment(
                    ad_photo_media,
                    ad_context.get("photo_url"),
                    "ad_image",
                    "Ad image",
                )
                if ad_photo_att:
                    ad_context_lines.append(
                        self._format_info_media_line("Ad image", ad_photo_att)
                    )

            conversation_info = "\n".join(info_lines)
            ad_context_str = "\n".join(ad_context_lines)

            # Build turns from sorted messages
            if not valid_items:
                return {"conversation_info": conversation_info, "ad_context": ad_context_str, "turns": [], "message_ref_map": {}}

            sorted_items = sorted(
                valid_items, key=lambda x: self._message_timestamp_seconds(x)
            )

            mid_to_index, index_to_mid = self._build_message_index_maps(sorted_items)

            # Map each message to a role + content line(s)
            raw_turns: List[Dict[str, str]] = []
            for seq, item in enumerate(sorted_items, 1):
                ts_sec = self._message_timestamp_seconds(item)
                ts = format_timestamp(ts_sec)
                ts_compact = (ts or "")[:16] if ts else ""
                is_echo = bool(item.get("is_echo", False))
                role = "assistant" if is_echo else "user"

                # Build sender label for the text line
                metadata = ensure_dict(item.get("metadata"))
                sent_by_ai = (
                    is_echo
                    and metadata is not None
                    and metadata.get("sent_by") == "ai_agent"
                )
                if not is_echo:
                    sender = "User"
                elif sent_by_ai:
                    sender = "Page (AI)"
                else:
                    sender = "Page"

                reply_tag = self._build_reply_tag(item.get("reply_to_message_id"), mid_to_index)

                msg_lines: List[str] = []
                text_value = item.get("text")
                text = (
                    text_value
                    if isinstance(text_value, str) and text_value
                    else (text_value or "")
                )
                if text:
                    msg_lines.append(f"[{ts_compact}] #{seq} {sender}:{reply_tag} {text}")
                else:
                    msg_lines.append(f"[{ts_compact}] #{seq} {sender}:{reply_tag}")

                photo_media = ensure_dict(item.get("photo_media"))
                photo_att = self._build_media_attachment(
                    photo_media,
                    item.get("photo_url"),
                    "message_image",
                    "Message image",
                )
                # User messages: if image URL is live, attach image directly (no description line).
                # Assistant messages: always text-only (no image block in role assistant).
                user_live_image_url = None
                if photo_att and role == "user" and photo_att.get("url"):
                    user_live_image_url = photo_att["url"]
                elif photo_att:
                    msg_lines.append(self._format_message_attachment_line(photo_att))
                video_url = extract_url(item.get("video_url"))
                if video_url:
                    msg_lines.append(f"[Attachment: video, url: {video_url}]")
                audio_url = extract_url(item.get("audio_url"))
                if audio_url:
                    msg_lines.append(f"[Attachment: audio, url: {audio_url}]")

                content_text = "\n".join(msg_lines)
                if user_live_image_url:
                    raw_turns.append({
                        "role": role,
                        "part": {"text": content_text, "image_url": user_live_image_url},
                    })
                else:
                    raw_turns.append({"role": role, "part": content_text})

            # Merge consecutive same-role turns (keep individual messages as separate parts)
            merged_turns: List[Dict[str, Any]] = []
            for turn in raw_turns:
                part = turn["part"]
                if merged_turns and merged_turns[-1]["role"] == turn["role"]:
                    merged_turns[-1]["content_parts"].append(part)
                else:
                    merged_turns.append(
                        {"role": turn["role"], "content_parts": [part]}
                    )

            return {
                "conversation_info": conversation_info,
                "ad_context": ad_context_str,
                "turns": merged_turns,
                "message_ref_map": index_to_mid,
            }

        except Exception as exc:
            logger.error("Error formatting messages as turns: %s", exc)
            return None

    @staticmethod
    def _build_message_index_maps(
        sorted_items: List[Dict[str, Any]],
    ) -> tuple:
        """Build bidirectional maps between Facebook message IDs and short #N indexes.

        Returns (mid_to_index, index_to_mid) where:
          mid_to_index: {"m_xxx": 1, "m_yyy": 2, ...}
          index_to_mid: {"#1": "m_xxx", "#2": "m_yyy", ...}
        """
        mid_to_index: Dict[str, int] = {}
        index_to_mid: Dict[str, str] = {}
        for seq, item in enumerate(sorted_items, 1):
            mid = item.get("id")
            if mid:
                mid_to_index[mid] = seq
                index_to_mid[f"#{seq}"] = mid
        return mid_to_index, index_to_mid

    @staticmethod
    def _build_reply_tag(
        reply_to_mid: Optional[str],
        mid_to_index: Dict[str, int],
    ) -> str:
        """Build a compact reply tag using short #N index when possible."""
        if not reply_to_mid:
            return ""
        ref_idx = mid_to_index.get(reply_to_mid)
        if ref_idx is not None:
            return f" [↩ #{ref_idx}]"
        return f" [↩ reply to mid: {reply_to_mid}]"

    def _format_message_attachment_line(self, att: Dict[str, Any]) -> str:
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
        ts = item.get("facebook_timestamp") or item.get("created_at") or 0
        return self._normalize_timestamp_seconds(ts)

    def _format_info_media_line(self, label: str, att: Dict[str, Any]) -> str:
        desc = att.get("description") or ""
        media_id = att.get("media_id") or ""
        media_ref = f", media_id: {media_id}" if media_id else ""
        if desc:
            return f"{label}: {desc}{media_ref}"
        return f"{label}:{media_ref}"

    def _format_image_tag(self, image_type: str, att: Dict[str, Any]) -> str:
        """Format avatar/image as a self-closing <image> tag."""
        media_id = att.get("media_id") or ""
        desc = att.get("description") or ""
        parts = [f'type="{image_type}"']
        if media_id:
            parts.append(f'media_id="{media_id}"')
        if desc:
            desc_safe = desc.replace('"', "&quot;")
            parts.append(f'description="{desc_safe}"')
        return f'<image {" ".join(parts)}/>'

    def _build_media_attachment(
        self,
        media_info: Optional[Dict[str, Any]],
        fallback_url: Optional[str],
        image_type: str,
        context_label: str,
    ) -> Optional[Dict[str, Any]]:
        media_dict = ensure_dict(media_info)
        has_source = self.media_asset_service.media_has_source(media_dict) or bool(
            fallback_url
        )
        if not has_source:
            return None

        is_active = self.media_asset_service.media_is_active(media_dict)
        description = None
        if media_dict:
            description = media_dict.get("description")
            if not description and media_dict.get("status") == "failed":
                description = f"[Image unavailable - {media_dict.get('error') or 'download_failed'}]"
            elif not description and not is_active:
                description = "[Image unavailable - expired]"

        url = None
        if is_active:
            url = media_dict.get("s3_url")
        elif fallback_url and not media_dict:
            url = fallback_url

        media_id = None
        if media_dict and media_dict.get("id") is not None:
            raw = media_dict.get("id")
            media_id = str(raw) if hasattr(raw, "__str__") else raw

        return {
            "type": image_type,
            "url": url,
            "description": description,
            "media_id": media_id,
        }

    def _build_conversation_text(
        self,
        fb_data: Dict[str, Any],
        conv_id: str,
    ) -> Optional[str]:
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

        mid_to_index, _ = self._build_message_index_maps(sorted_items)

        for seq, item in enumerate(sorted_items, 1):
            ts_sec = self._message_timestamp_seconds(item)
            ts = format_timestamp(ts_sec)
            ts_compact = (ts or "")[:16] if ts else ""
            is_echo = bool(item.get("is_echo", False))
            metadata = ensure_dict(item.get("metadata"))
            sent_by_ai = (
                is_echo
                and metadata is not None
                and metadata.get("sent_by") == "ai_agent"
            )
            if not is_echo:
                sender = "User"
            elif sent_by_ai:
                sender = "Page (AI)"
            else:
                sender = "Page"
            reply_tag = self._build_reply_tag(item.get("reply_to_message_id"), mid_to_index)

            text_value = item.get("text")
            text = (
                text_value
                if isinstance(text_value, str) and text_value
                else (text_value or "")
            )
            if text:
                lines.append(f"[{ts_compact}] #{seq} {sender}:{reply_tag} {text}")
            else:
                lines.append(f"[{ts_compact}] #{seq} {sender}:{reply_tag}")

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
    ) -> Optional[str]:
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

        page_label = f"**{page_name}**" + (f" ({category})" if category else "")
        lines: List[str] = [
            "=== Comment Thread Info ===",
            f"Page: {page_label}",
        ]
        page_avatar_media = ensure_dict(page_info_raw.get("avatar_media"))
        page_avatar_att = self._build_media_attachment(
            page_avatar_media,
            page_info_raw.get("avatar"),
            "avatar_image",
            "Page avatar",
        )
        if page_avatar_att:
            lines.append(self._format_image_tag("page_avatar", page_avatar_att))
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

        comment_by_id: Dict[str, Dict[str, Any]] = {
            str(c.get("id", "")): c for c in valid_comments
        }

        def depth(c: Dict[str, Any]) -> int:
            pid = c.get("parent_comment_id")
            if pid is None or not str(pid):
                return 0
            parent = comment_by_id.get(str(pid))
            return 0 if not parent else 1 + depth(parent)

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
            metadata = ensure_dict(comment.get("metadata"))
            sent_by_ai = metadata is not None and metadata.get("sent_by") == "ai_agent"
            if author_is_page and sent_by_ai:
                role_label = "Page (AI)"
            elif author_is_page:
                role_label = "Page"
            else:
                role_label = f"User ({author_name})"
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


__all__ = ["FacebookContentFormatter"]
