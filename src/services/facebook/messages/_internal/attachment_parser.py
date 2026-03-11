"""
Attachment parsing utilities for Facebook messages.

Handles parsing of various attachment types (photo, video, audio, template)
and entry point data from referrals.
"""

import copy
from typing import Any, Dict, List, Optional


class AttachmentParser:
    """Parses Facebook message attachments and entry points."""

    @staticmethod
    def parse_attachments(attachments: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Parse message attachments and extract URLs by type.

        Args:
            attachments: List of attachment objects from message

        Returns:
            Dictionary with parsed attachment URLs:
            - photo: Photo/image URL
            - video: Video URL
            - audio: Audio URL
            - template: Template/fallback data
        """
        result: Dict[str, Any] = {
            "photo": None,
            "video": None,
            "audio": None,
            "template": None,
        }

        if not attachments:
            return result

        for attachment in attachments:
            attachment_type = attachment.get("type", "")
            payload = attachment.get("payload", {})
            media = attachment.get("media", {})
            target = attachment.get("target", {})
            image_data = attachment.get("image_data", {})
            mime_type = attachment.get("mime_type", "")

            # Try multiple sources for URL (Graph API vs Webhook format)
            # Graph API format: image_data.url, video_data.url, etc.
            # Webhook format: payload.url, media.image.src
            url = (
                image_data.get("url")  # Graph API image format
                or attachment.get("video_data", {}).get("url")  # Graph API video format
                or attachment.get("audio_data", {}).get("url")  # Graph API audio format
                or attachment.get("url")  # Direct URL (Graph API format)
                or payload.get("url")  # Webhook format
                or (media.get("image", {}) or {}).get("src")  # Media image src
                or target.get("url")  # Target URL
            )

            # Determine attachment type from mime_type if type is empty (Graph API format)
            if not attachment_type and mime_type:
                if mime_type.startswith("image/"):
                    attachment_type = "image"
                elif mime_type.startswith("video/"):
                    attachment_type = "video"
                elif mime_type.startswith("audio/"):
                    attachment_type = "audio"

            # Also check if image_data exists (Graph API format for images)
            if not attachment_type and image_data:
                attachment_type = "image"

            if attachment_type in ["image", "photo"]:
                result["photo"] = url
            elif attachment_type == "video":
                result["video"] = url
            elif attachment_type == "audio":
                result["audio"] = url
            elif attachment_type == "template":
                result["template"] = attachment
            elif attachment_type == "fallback":
                result["template"] = attachment

        return result

    @staticmethod
    def build_entry_point(
        referral: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        Build entry point data from referral information.

        Args:
            referral: Referral data from message/postback

        Returns:
            Structured entry point data or None
        """
        if not referral:
            return None

        # Extract ads_context_data if available
        ads_context = referral.get("ads_context_data", {})

        return {
            "source": referral.get("source"),
            "type": referral.get("type"),
            "ad_id": referral.get("ad_id"),
            "ad_link": referral.get("ad_link"),
            "ref": referral.get("ref"),
            "referer_uri": referral.get("referer_uri"),
            # Extract ads_context_data fields
            "ad_title": ads_context.get("ad_title"),
            "photo_url": ads_context.get("photo_url"),
            "video_url": ads_context.get("video_url"),
            "post_id": ads_context.get("post_id"),
            "product_id": ads_context.get("product_id"),
            "raw": referral,
        }

    @staticmethod
    def merge_entry_point(
        template_data: Optional[Dict[str, Any]],
        entry_point: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        Merge entry point data into template data.

        Args:
            template_data: Existing template data (may be None)
            entry_point: Entry point to merge

        Returns:
            Merged data with entry_point field, or original if no entry_point
        """
        if not entry_point:
            return template_data

        base = copy.deepcopy(template_data) if template_data else {}
        base["entry_point"] = entry_point
        return base


# Module-level convenience functions
parse_attachments = AttachmentParser.parse_attachments
build_entry_point = AttachmentParser.build_entry_point
merge_entry_point = AttachmentParser.merge_entry_point
