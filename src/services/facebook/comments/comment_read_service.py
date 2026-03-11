"""
Comment Read Service.

Service for reading Facebook comment threads.
Extracted from facebook_read_service.py for domain organization.
"""

from typing import Any, Dict, List, Optional, Tuple

from src.database.postgres.repositories.facebook_queries.comments import (
    get_comments_by_root_comment_id,
    get_conversation_by_id,
    get_page_info_by_root_comment_id,
    get_post_info_by_root_comment_id,
)
from src.database.postgres.repositories.facebook_queries.comments.comment_conversations import (
    list_conversations_for_pages,
)
from src.database.postgres.repositories.facebook_queries.comments.comment_posts import (
    get_post_by_id,
)
from src.utils.logger import get_logger

logger = get_logger()


class CommentReadService:
    """
    Service for reading Facebook comment threads.
    Returns raw data without formatting - formatting is handled by the agent layer.
    """

    async def get_comment_thread_paginated(
        self,
        conn,
        root_comment_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[Optional[Dict[str, Any]], int, bool]:
        """
        Get comment thread with pagination.

        Args:
            conn: Database connection
            root_comment_id: Root comment ID or conversation ID
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Tuple of (fb_data, total_count, has_next_page)
            fb_data contains raw comment thread data ready for formatting
        """
        try:
            resolved_root_comment_id = root_comment_id
            comments = await get_comments_by_root_comment_id(
                conn, resolved_root_comment_id
            )
            if not comments:
                # Allow callers to pass the conversation_id (facebook_conversation_comments.id).
                conversation = await get_conversation_by_id(conn, root_comment_id)
                if conversation and conversation.get("root_comment_id"):
                    resolved_root_comment_id = conversation["root_comment_id"]
                    comments = await get_comments_by_root_comment_id(
                        conn, resolved_root_comment_id
                    )
            if not comments:
                return None, 0, False

            page_info = await get_page_info_by_root_comment_id(
                conn, resolved_root_comment_id
            )
            if not page_info:
                return None, 0, False
            post_info = await get_post_info_by_root_comment_id(
                conn, resolved_root_comment_id
            )
            if not post_info:
                post_info = {}

            formatted_comments = []
            for comment in comments:
                # Use photo_media from comment if available (already built by query with id)
                comment_photo_media = comment.get("photo_media")
                if not comment_photo_media:
                    # Convert UUID to string if present
                    photo_media_id_raw = comment.get("comment_photo_media_id")
                    photo_media_id = None
                    if photo_media_id_raw is not None:
                        photo_media_id = (
                            str(photo_media_id_raw)
                            if hasattr(photo_media_id_raw, "__str__")
                            else photo_media_id_raw
                        )

                    comment_photo_media = {
                        "id": photo_media_id,
                        "s3_url": comment.get("comment_photo_s3_url"),
                        "status": comment.get("comment_photo_s3_status"),
                        "retention_policy": comment.get(
                            "comment_photo_retention_policy"
                        ),
                        "expires_at": comment.get("comment_photo_expires_at"),
                        "original_url": comment.get("photo_url"),
                    }

                # Use fpsu_avatar_media from comment if available (already built by query with id)
                fpsu_avatar_media = comment.get("fpsu_avatar_media")
                if not fpsu_avatar_media:
                    # Convert UUID to string if present
                    avatar_media_id_raw = comment.get("fpsu_avatar_media_id")
                    avatar_media_id = None
                    if avatar_media_id_raw is not None:
                        avatar_media_id = (
                            str(avatar_media_id_raw)
                            if hasattr(avatar_media_id_raw, "__str__")
                            else avatar_media_id_raw
                        )

                    fpsu_avatar_media = {
                        "id": avatar_media_id,
                        "s3_url": comment.get("fpsu_avatar_s3_url"),
                        "status": comment.get("fpsu_avatar_s3_status"),
                        "retention_policy": comment.get("fpsu_avatar_retention_policy"),
                        "expires_at": comment.get("fpsu_avatar_expires_at"),
                        "original_url": comment.get("fpsu_profile_pic"),
                    }
                formatted_comments.append(
                    {
                        "id": comment["id"],
                        "message": comment.get("message", ""),
                        "is_from_page": comment.get("is_from_page", False),
                        "parent_comment_id": comment.get("parent_comment_id"),
                        "photo_url": comment.get("photo_url"),
                        "video_url": comment.get("video_url"),
                        "fpsu_name": comment.get("fpsu_name"),
                        "fpsu_id": comment.get("fpsu_id"),
                        "fpsu_profile_pic": comment.get("fpsu_profile_pic"),
                        "fpsu_avatar_media": fpsu_avatar_media,
                        "mark_as_read": comment.get("mark_as_read", False),
                        "created_at": comment.get("facebook_created_time")
                        or comment.get("created_at", 0),
                        "facebook_created_time": comment.get("facebook_created_time"),
                        "metadata": comment.get("metadata"),
                        "photo_media": comment_photo_media,
                    }
                )

            # Sort by created_at ascending (oldest first)
            formatted_comments.sort(key=lambda x: x.get("created_at", 0))
            total_count = len(formatted_comments)

            # Apply offset-based pagination
            page = max(1, page)
            page_size = max(1, min(page_size, 100))
            offset = (page - 1) * page_size
            paginated = formatted_comments[offset : offset + page_size]
            has_next_page = (offset + len(paginated)) < total_count

            fb_data = {
                "root_comment_id": resolved_root_comment_id,
                "comments": paginated,
                "total_count": total_count,
                "has_next_page": has_next_page,
                "page_info": {
                    "id": page_info.get("id"),
                    "name": page_info.get("name", "Unknown Page"),
                    "avatar": page_info.get("avatar"),
                    "category": page_info.get("category"),
                    "fan_count": page_info.get("fan_count"),
                    "followers_count": page_info.get("followers_count"),
                    "rating_count": page_info.get("rating_count"),
                    "overall_star_rating": (
                        float(page_info.get("overall_star_rating"))
                        if page_info.get("overall_star_rating") is not None
                        else None
                    ),
                    "about": page_info.get("about"),
                    "description": page_info.get("description"),
                    "link": page_info.get("link"),
                    "website": page_info.get("website"),
                    "phone": page_info.get("phone"),
                    "emails": page_info.get("emails"),
                    "location": page_info.get("location"),
                    "cover": page_info.get("cover"),
                    "hours": page_info.get("hours"),
                    "is_verified": page_info.get("is_verified"),
                    "avatar_media": page_info.get("avatar_media")
                    or {
                        "id": None,
                        "s3_url": None,
                        "status": None,
                        "retention_policy": None,
                        "expires_at": None,
                        "original_url": page_info.get("avatar"),
                    },
                },
                "post": {
                    "id": post_info.get("id"),
                    "message": post_info.get("message", ""),
                    "photo_link": post_info.get("photo_link"),
                    "video_link": post_info.get("video_link"),
                    "facebook_created_time": post_info.get("facebook_created_time"),
                    "full_picture": post_info.get(
                        "full_picture"
                    ),  # Add full_picture for fallback
                    "photo_media": post_info.get("photo_media")
                    or {
                        "id": None,
                        "s3_url": post_info.get("post_photo_s3_url"),
                        "status": post_info.get("post_photo_s3_status"),
                        "retention_policy": post_info.get(
                            "post_photo_retention_policy"
                        ),
                        "expires_at": post_info.get("post_photo_expires_at"),
                        "original_url": post_info.get("photo_link")
                        or post_info.get("full_picture"),
                    },
                },
            }

            return fb_data, total_count, has_next_page
        except Exception as exc:
            logger.error("Error getting FB comments from DB: %s", exc)
            return None, 0, False

    async def list_comment_threads_by_post(
        self,
        conn,
        post_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        filter_type: str = "all",
        sort_by: str = "recent",
    ) -> Tuple[List[Dict[str, Any]], int, bool]:
        """
        List comment threads (conversations) for a post.

        Args:
            conn: Database connection
            post_id: Post ID
            limit: Max threads to return (1-100)
            offset: Offset for pagination
            filter_type: "all", "has_page_reply", "no_page_reply", or "unread"
            sort_by: "recent" or "top_engagement"

        Returns:
            Tuple of (threads list, total_count, has_more)
        """
        try:
            # First get the page_id for this post
            post = await get_post_by_id(conn, post_id)
            if not post:
                return [], 0, False

            page_id = post.get("fan_page_id")
            if not page_id:
                return [], 0, False

            cursor = None
            threads, has_more, next_cursor = await list_conversations_for_pages(
                conn, [page_id], limit=limit + offset, cursor=cursor
            )

            # Filter by post_id
            threads = [t for t in threads if t.get("post_id") == post_id]

            # Apply filters
            if filter_type == "has_page_reply":
                threads = [t for t in threads if t.get("has_page_reply", False)]
            elif filter_type == "no_page_reply":
                threads = [t for t in threads if not t.get("has_page_reply", False)]
            elif filter_type == "unread":
                threads = [t for t in threads if t.get("unread_count", 0) > 0]

            # Sort
            if sort_by == "top_engagement":
                threads.sort(key=lambda t: t.get("total_comments", 0), reverse=True)
            else:  # recent
                threads.sort(
                    key=lambda t: t.get("latest_comment_facebook_time")
                    or t.get("updated_at")
                    or 0,
                    reverse=True,
                )

            # Apply offset
            if offset > 0 and len(threads) > offset:
                threads = threads[offset:]
            elif offset > 0:
                threads = []
                has_more = False

            # Limit
            if len(threads) > limit:
                threads = threads[:limit]
                has_more = True

            total_count = len(threads) + (limit if has_more else 0)

            return threads, total_count, has_more
        except Exception as exc:
            logger.error("Error listing comment threads by post: %s", exc)
            return [], 0, False
