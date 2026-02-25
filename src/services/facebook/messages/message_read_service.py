"""
Message Read Service.

Service for reading Facebook conversation messages.
Extracted from facebook_read_service.py for domain organization.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from src.database.postgres.repositories.facebook_queries.messages import (
    list_messages_by_conversation_id_paginated,
)
from src.database.postgres.repositories.facebook_queries.messages.conversations import (
    get_conversation_metadata_with_media,
    list_conversations_by_page_ids,
)
from src.utils.logger import get_logger

logger = get_logger()


class MessageReadService:
    """
    Service for reading Facebook conversation messages.
    Returns raw data without formatting - formatting is handled by the agent layer.
    """

    async def get_conversation_messages_paginated(
        self,
        conn,
        conversation_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[Optional[Dict[str, Any]], int, bool]:
        """
        Get conversation messages with pagination.

        Returns:
            Tuple of (fb_data, total_count, has_next_page)
            fb_data contains raw conversation data ready for formatting
        """
        try:
            messages_list, total_count, has_next_page = (
                await list_messages_by_conversation_id_paginated(
                    conn, conversation_id, page=page, page_size=page_size
                )
            )

            if not messages_list:
                return None, 0, False

            conv_info = await get_conversation_metadata_with_media(
                conn, conversation_id
            )
            if not conv_info:
                return None, 0, False

            items = []
            for msg in messages_list:
                # Use photo_media from msg if available (already processed by _process_message_row with id)
                # Fallback to building it if not present (shouldn't happen, but safe)
                photo_media = msg.get("photo_media")
                if not photo_media:
                    # Convert UUID to string if present, otherwise keep None
                    photo_media_id_raw = msg.get("photo_media_id")
                    photo_media_id = None
                    if photo_media_id_raw is not None:
                        photo_media_id = (
                            str(photo_media_id_raw)
                            if hasattr(photo_media_id_raw, "__str__")
                            else photo_media_id_raw
                        )

                    photo_media = {
                        "id": photo_media_id,
                        "s3_url": msg.get("photo_s3_url"),
                        "status": msg.get("photo_s3_status"),
                        "retention_policy": msg.get("photo_retention_policy"),
                        "expires_at": msg.get("photo_expires_at"),
                        "original_url": msg.get("photo_url"),
                    }
                items.append(
                    {
                        "id": msg["id"],
                        "text": msg.get("text", ""),
                        "is_echo": msg.get("is_echo", False),
                        "metadata": msg.get("metadata"),
                        "photo_url": msg.get("photo_url"),
                        "video_url": msg.get("video_url"),
                        "audio_url": msg.get("audio_url"),
                        "created_at": msg.get("created_at", 0),
                        "facebook_timestamp": msg.get("facebook_timestamp"),
                        "photo_media": photo_media,
                        "reply_to_message_id": msg.get("reply_to_message_id"),
                    }
                )

            user_info_raw = conv_info.get("user_info") or {}
            if isinstance(user_info_raw, str):
                try:
                    user_info_raw = json.loads(user_info_raw)
                except (json.JSONDecodeError, TypeError):
                    user_info_raw = {}
            if not isinstance(user_info_raw, dict):
                user_info_raw = {}

            # Convert UUIDs to strings
            page_avatar_id_raw = conv_info.get("page_avatar_media_id")
            page_avatar_id = None
            if page_avatar_id_raw is not None:
                page_avatar_id = (
                    str(page_avatar_id_raw)
                    if hasattr(page_avatar_id_raw, "__str__")
                    else page_avatar_id_raw
                )

            user_avatar_id_raw = conv_info.get("user_avatar_media_id")
            user_avatar_id = None
            if user_avatar_id_raw is not None:
                user_avatar_id = (
                    str(user_avatar_id_raw)
                    if hasattr(user_avatar_id_raw, "__str__")
                    else user_avatar_id_raw
                )

            page_avatar_media = {
                "id": page_avatar_id,
                "s3_url": conv_info.get("page_avatar_s3_url"),
                "status": conv_info.get("page_avatar_s3_status"),
                "retention_policy": conv_info.get("page_avatar_retention_policy"),
                "expires_at": conv_info.get("page_avatar_expires_at"),
                "original_url": conv_info.get("page_avatar"),
            }
            user_avatar_media = {
                "id": user_avatar_id,
                "s3_url": conv_info.get("user_avatar_s3_url"),
                "status": conv_info.get("user_avatar_s3_status"),
                "retention_policy": conv_info.get("user_avatar_retention_policy"),
                "expires_at": conv_info.get("user_avatar_expires_at"),
                "original_url": user_info_raw.get("profile_pic"),
            }

            # Get ad_context from conversation
            ad_context = conv_info.get("ad_context")
            if isinstance(ad_context, str):
                try:
                    ad_context = json.loads(ad_context)
                except (json.JSONDecodeError, TypeError):
                    ad_context = None
            if not isinstance(ad_context, dict):
                ad_context = None

            # Get post_info if post_id exists in ad_context
            post_info = None
            if ad_context and ad_context.get("post_id"):
                post_id = ad_context.get("post_id")
                try:
                    from src.database.postgres.repositories.facebook_queries.comments.comment_posts import (
                        get_post_by_id,
                    )

                    post_info = await get_post_by_id(conn, post_id)
                    if post_info:
                        logger.info(
                            f"📢 Loaded post_info for post {post_id} from ad_context"
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to load post_info for post {post_id} from ad_context: {e}"
                    )
                    # Continue even if post_info fetch fails

            fb_data = {
                "items": items,
                "total_count": total_count,
                "has_next_page": has_next_page,
                "fan_page_id": conv_info.get("fan_page_id"),
                "facebook_page_scope_user_id": conv_info.get(
                    "facebook_page_scope_user_id"
                ),
                "page_info": {
                    "id": conv_info.get("fan_page_id"),
                    "name": conv_info.get("page_name", "Unknown Page"),
                    "avatar": conv_info.get("page_avatar"),
                    "avatar_media": page_avatar_media,
                    "category": conv_info.get("page_category"),
                    "fan_count": conv_info.get("page_fan_count"),
                    "followers_count": conv_info.get("page_followers_count"),
                    "rating_count": conv_info.get("page_rating_count"),
                    "overall_star_rating": (
                        float(conv_info.get("page_overall_star_rating"))
                        if conv_info.get("page_overall_star_rating") is not None
                        else None
                    ),
                    "about": conv_info.get("page_about"),
                    "description": conv_info.get("page_description"),
                    "link": conv_info.get("page_link"),
                    "website": conv_info.get("page_website"),
                    "phone": conv_info.get("page_phone"),
                    "emails": conv_info.get("page_emails"),
                    "location": conv_info.get("page_location"),
                    "cover": conv_info.get("page_cover"),
                    "hours": conv_info.get("page_hours"),
                    "is_verified": conv_info.get("page_is_verified"),
                },
                "user_info": {
                    **user_info_raw,
                    "id": user_info_raw.get("id")
                    or conv_info.get("facebook_page_scope_user_id"),
                    "avatar_media": user_avatar_media,
                },
            }

            # Add ad_context if available
            if ad_context:
                fb_data["ad_context"] = ad_context
                if post_info:
                    fb_data["post_info"] = post_info

            return fb_data, total_count, has_next_page
        except Exception as exc:
            logger.error("Error getting FB conversation messages from DB: %s", exc)
            return None, 0, False

    async def list_inbox_conversations(
        self,
        conn,
        page_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        filter_type: str = "all",
    ) -> Tuple[List[Dict[str, Any]], int, bool]:
        """
        List inbox conversations for a page.

        Args:
            conn: Database connection
            page_id: Facebook page ID
            limit: Max conversations to return (1-100)
            offset: Offset for pagination
            filter_type: "all" or "unread"

        Returns:
            Tuple of (conversations list, total_count, has_more)
        """
        try:
            # Fetch more than needed to handle filtering and offset
            fetch_limit = limit + offset + 50  # Extra buffer for filtering
            cursor = None

            conversations, has_more, next_cursor = await list_conversations_by_page_ids(
                conn, [page_id], limit=fetch_limit, cursor=cursor
            )

            # Filter by unread if needed
            if filter_type == "unread":
                conversations = [
                    c
                    for c in conversations
                    if c.get("unread_count", 0) > 0 or not c.get("mark_as_read", False)
                ]

            # Calculate total before offset
            total_count = len(conversations)
            if has_more:
                total_count += 50  # Approximate

            # Apply offset
            if offset > 0 and len(conversations) > offset:
                conversations = conversations[offset:]
            elif offset > 0:
                conversations = []

            # Limit to requested limit
            if len(conversations) > limit:
                conversations = conversations[:limit]
                has_more = True
            else:
                # Adjust has_more based on whether we got full fetch_limit
                has_more = (len(conversations) == limit) and (
                    total_count > offset + limit
                )

            return conversations, total_count, has_more
        except Exception as exc:
            logger.error("Error listing inbox conversations: %s", exc)
            return [], 0, False
