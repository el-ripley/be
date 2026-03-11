"""Post read service - Read-only operations for Facebook posts."""

from typing import Any, Dict, List, Optional, Tuple

from src.database.postgres.repositories.facebook_queries.comments.comment_posts import (
    get_post_by_id,
)
from src.database.postgres.repositories.facebook_queries.comments.comment_posts import (
    list_posts_by_page as query_list_posts_by_page,
)
from src.database.postgres.repositories.facebook_queries.reactions import (
    get_post_reactions,
)
from src.utils.logger import get_logger

logger = get_logger()


class PostReadService:
    """
    Service for reading Facebook posts.
    Returns raw data without formatting - formatting is handled by the agent layer.
    """

    async def list_posts_by_page(
        self,
        conn,
        page_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        time_range_days: Optional[int] = None,
        sort_by: str = "recent",
    ) -> Tuple[List[Dict[str, Any]], int, bool]:
        """
        List posts for a page with pagination.

        Args:
            conn: Database connection
            page_id: Facebook page ID
            limit: Max posts to return (1-100)
            offset: Offset for pagination (for offset-based pagination)
            time_range_days: Optional filter for posts from last N days
            sort_by: "recent" or "top_engagement"

        Returns:
            Tuple of (posts list, total_count, has_more)
        """
        try:
            # Convert offset to cursor-based if needed
            # For now, use cursor=None (start from beginning)
            cursor = None

            # Build time filter if needed
            if time_range_days:
                from src.database.postgres.utils import get_current_timestamp

                current_time = get_current_timestamp()
                min_time = current_time - (time_range_days * 86400)  # days to seconds
                # We'll filter in Python for now since query doesn't support it
                # TODO: Add time filter to query if needed

            posts, has_more, next_cursor = await query_list_posts_by_page(
                conn, page_id, limit=limit, cursor=cursor
            )

            # Filter by time_range if needed
            if time_range_days and posts:
                from src.database.postgres.utils import get_current_timestamp

                current_time = get_current_timestamp()
                min_time = current_time - (time_range_days * 86400)
                posts = [
                    p
                    for p in posts
                    if (p.get("facebook_created_time") or 0) >= min_time
                ]

            # Sort by engagement if requested
            if sort_by == "top_engagement":
                posts.sort(
                    key=lambda p: (
                        p.get("reaction_total_count", 0)
                        + p.get("comment_count", 0) * 2
                        + p.get("share_count", 0) * 3
                    ),
                    reverse=True,
                )

            # Calculate total (approximate for now)
            total_count = len(posts) + (limit if has_more else 0)

            return posts, total_count, has_more
        except Exception as exc:
            logger.error("Error listing posts by page: %s", exc)
            return [], 0, False

    async def get_post_with_engagement(
        self,
        conn,
        post_id: str,
        *,
        include_top_reactors: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Get post details with full engagement breakdown.

        Args:
            conn: Database connection
            post_id: Post ID
            include_top_reactors: Whether to include top reactors list

        Returns:
            Post data with engagement or None
        """
        try:
            post = await get_post_by_id(conn, post_id)
            if not post:
                return None

            result = dict(post)

            # Add reactions if requested
            if include_top_reactors:
                reactions = await get_post_reactions(conn, post_id)
                # Group by type and get top ones
                reactors_by_type = {}
                for r in reactions:
                    r_type = r.get("reaction_type")
                    if r_type:
                        if r_type not in reactors_by_type:
                            reactors_by_type[r_type] = []
                        reactors_by_type[r_type].append(
                            {
                                "name": r.get("reactor_name"),
                                "reactor_id": r.get("reactor_id"),
                                "profile_pic": r.get("reactor_profile_pic"),
                            }
                        )

                result["top_reactors"] = reactors_by_type
            else:
                result["top_reactors"] = None

            return result
        except Exception as exc:
            logger.error("Error getting post with engagement: %s", exc)
            return None

    async def list_posts(
        self,
        conn,
        fan_page_id: str,
        limit: int = 20,
        cursor: Optional[Tuple[int, str]] = None,
        need_comment_sync: Optional[bool] = None,
    ) -> Tuple[List[Dict[str, Any]], bool, Optional[Tuple[int, str]]]:
        """
        List posts for a page with cursor-based pagination.

        Args:
            conn: Database connection
            fan_page_id: Page ID
            limit: Max posts to return (1-100)
            cursor: Optional cursor tuple (facebook_created_time, post_id) for pagination
            need_comment_sync: Optional filter - True = only posts needing comment sync,
                              False = only posts with completed comment sync, None = all

        Returns:
            Tuple of (posts list, has_more, next_cursor)
        """
        return await query_list_posts_by_page(
            conn=conn,
            fan_page_id=fan_page_id,
            limit=limit,
            cursor=cursor,
            need_comment_sync=need_comment_sync,
        )
