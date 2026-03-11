"""
Service for Agent-triggered engagement data refresh.

This service is NOT called by User APIs - only by Agent when it decides data is stale.
"""

from typing import Any, Dict, List

from src.common.clients.facebook_graph_page_client import FacebookGraphPageClient
from src.database.postgres.repositories.facebook_queries import (
    get_post_by_id,
    update_comment_engagement,
    update_post_engagement,
    upsert_comment_reactions,
    upsert_post_reactions,
)
from src.services.facebook._core.helpers import execute_graph_client_with_random_tokens
from src.utils.logger import get_logger

logger = get_logger()


class EngagementRefetchService:
    """
    Service for Agent-triggered engagement data refresh.

    NOT called by User APIs - only by Agent when it decides data is stale.
    """

    async def refetch_post_engagement(
        self,
        conn,
        post_id: str,
        page_admins: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Agent calls this to get fresh engagement data for a post.

        1. Fetch from Graph API (get_post_engagement)
        2. Update posts table with new counts
        3. Sync reactions list to post_reactions table
        4. Return fresh data

        Args:
            conn: Database connection
            post_id: Facebook post ID
            page_admins: List of page admins for token management

        Returns:
            Dictionary with fresh engagement data and update status
        """
        if not page_admins:
            logger.error(
                f"❌ No page admins found for page when refetching post {post_id}"
            )
            return {
                "success": False,
                "message": "No page admins available",
                "post_id": post_id,
            }

        # Define callback to fetch post engagement from Facebook Graph API
        async def fetch_engagement_callback(client: FacebookGraphPageClient):
            return await client.get_post_engagement(post_id, reactions_limit=100)

        # Execute the Facebook API call with token retry logic
        engagement_data = await execute_graph_client_with_random_tokens(
            page_admins,
            fetch_engagement_callback,
            f"refetch post engagement for {post_id}",
        )

        if not engagement_data:
            logger.error(
                f"❌ Failed to refetch post engagement from Facebook: {post_id}"
            )
            return {
                "success": False,
                "message": "Failed to fetch engagement data",
                "post_id": post_id,
            }

        # Extract engagement data
        reaction_data = engagement_data.get("reactions", {})
        reaction_summary = reaction_data.get("summary", {})
        reaction_total = reaction_summary.get("total_count", 0)
        reactions_list = reaction_data.get("data", [])

        # Count reactions by type
        reaction_counts = self._count_reactions_by_type(reactions_list)

        shares_data = engagement_data.get("shares", {})
        share_count = shares_data.get("count", 0) if shares_data else 0

        comments_data = engagement_data.get("comments", {})
        comment_count = (
            comments_data.get("summary", {}).get("total_count", 0)
            if comments_data
            else 0
        )

        # Get current timestamp
        from src.database.postgres.utils import get_current_timestamp

        now_timestamp = get_current_timestamp()

        # Get post to find fan_page_id
        post = await get_post_by_id(conn, post_id)
        if not post:
            logger.error(f"❌ Post not found in database: {post_id}")
            return {
                "success": False,
                "message": "Post not found in database",
                "post_id": post_id,
            }

        fan_page_id = post["fan_page_id"]

        # Update post engagement in database
        _ = await update_post_engagement(
            conn=conn,
            post_id=post_id,
            reaction_total_count=reaction_total,
            reaction_like_count=reaction_counts.get("LIKE", 0),
            reaction_love_count=reaction_counts.get("LOVE", 0),
            reaction_haha_count=reaction_counts.get("HAHA", 0),
            reaction_wow_count=reaction_counts.get("WOW", 0),
            reaction_sad_count=reaction_counts.get("SAD", 0),
            reaction_angry_count=reaction_counts.get("ANGRY", 0),
            reaction_care_count=reaction_counts.get("CARE", 0),
            share_count=share_count,
            comment_count=comment_count,
            full_picture=engagement_data.get("full_picture"),
            permalink_url=engagement_data.get("permalink_url"),
            status_type=engagement_data.get("status_type"),
            is_published=engagement_data.get("is_published"),
            engagement_fetched_at=now_timestamp,
        )

        # Sync reactions list to post_reactions table
        if reactions_list:
            # Normalize reactions: if reactor_id == page_id, set to None (page reaction)
            normalized_reactions = self._normalize_reactions(
                reactions_list, fan_page_id
            )
            await upsert_post_reactions(
                conn=conn,
                post_id=post_id,
                fan_page_id=fan_page_id,
                reactions_list=normalized_reactions,
            )

        return {
            "success": True,
            "post_id": post_id,
            "engagement": {
                "reaction_total_count": reaction_total,
                "reaction_like_count": reaction_counts.get("LIKE", 0),
                "reaction_love_count": reaction_counts.get("LOVE", 0),
                "reaction_haha_count": reaction_counts.get("HAHA", 0),
                "reaction_wow_count": reaction_counts.get("WOW", 0),
                "reaction_sad_count": reaction_counts.get("SAD", 0),
                "reaction_angry_count": reaction_counts.get("ANGRY", 0),
                "reaction_care_count": reaction_counts.get("CARE", 0),
                "share_count": share_count,
                "comment_count": comment_count,
            },
            "reactions_list": reactions_list,
            "refetched_at": now_timestamp,
            "db_updated": True,
        }

    async def refetch_comment_reactions(
        self,
        conn,
        comment_id: str,
        page_admins: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Agent calls this to get fresh comment reactions.

        1. Fetch from Graph API (get_comment_reactions)
        2. Update comments table with new like_count
        3. Sync reactions list to comment_reactions table
        4. Return fresh data

        Args:
            conn: Database connection
            comment_id: Facebook comment ID
            page_admins: List of page admins for token management

        Returns:
            Dictionary with fresh reactions data and update status
        """
        if not page_admins:
            logger.error(f"❌ No page admins found when refetching comment {comment_id}")
            return {
                "success": False,
                "message": "No page admins available",
                "comment_id": comment_id,
            }

        # Define callback to fetch comment reactions from Facebook Graph API
        async def fetch_reactions_callback(client: FacebookGraphPageClient):
            return await client.get_comment_reactions(comment_id, limit=100)

        # Execute the Facebook API call with token retry logic
        reactions_data = await execute_graph_client_with_random_tokens(
            page_admins,
            fetch_reactions_callback,
            f"refetch comment reactions for {comment_id}",
        )

        if not reactions_data:
            logger.error(
                f"❌ Failed to refetch comment reactions from Facebook: {comment_id}"
            )
            return {
                "success": False,
                "message": "Failed to fetch reactions data",
                "comment_id": comment_id,
            }

        # Extract reactions data
        reaction_data = reactions_data.get("reactions", {})
        reaction_summary = reaction_data.get("summary", {})
        reaction_total = reaction_summary.get("total_count", 0)
        reactions_list = reaction_data.get("data", [])
        like_count = reactions_data.get("like_count", 0)

        # Get current timestamp
        from src.database.postgres.utils import get_current_timestamp

        now_timestamp = get_current_timestamp()

        # Get comment to find post_id and fan_page_id
        from src.database.postgres.repositories.facebook_queries import get_comment

        comment = await get_comment(conn, comment_id)
        if not comment:
            logger.error(f"❌ Comment not found in database: {comment_id}")
            return {
                "success": False,
                "message": "Comment not found in database",
                "comment_id": comment_id,
            }

        post_id = comment["post_id"]
        fan_page_id = comment["fan_page_id"]

        # Update comment engagement in database
        _ = await update_comment_engagement(
            conn=conn,
            comment_id=comment_id,
            like_count=like_count,
            reactions_fetched_at=now_timestamp,
        )

        # Sync reactions list to comment_reactions table
        if reactions_list:
            # Normalize reactions: if reactor_id == page_id, set to None (page reaction)
            normalized_reactions = self._normalize_reactions(
                reactions_list, fan_page_id
            )
            await upsert_comment_reactions(
                conn=conn,
                comment_id=comment_id,
                post_id=post_id,
                fan_page_id=fan_page_id,
                reactions_list=normalized_reactions,
            )

        return {
            "success": True,
            "comment_id": comment_id,
            "like_count": like_count,
            "reaction_total_count": reaction_total,
            "reactions_list": reactions_list,
            "refetched_at": now_timestamp,
            "db_updated": True,
        }

    def _normalize_reactions(
        self, reactions_list: List[Dict[str, Any]], page_id: str
    ) -> List[Dict[str, Any]]:
        """
        Normalize reactions list: if reactor_id == page_id, set id to None (page reaction).

        Args:
            reactions_list: List of reaction dicts from Facebook API
            page_id: Facebook page ID

        Returns:
            Normalized reactions list where page reactions have id=None
        """
        normalized = []
        for reaction in reactions_list:
            reactor_id = reaction.get("id")
            # If reactor is the page itself, set id to None
            if reactor_id == page_id:
                normalized_reaction = reaction.copy()
                normalized_reaction["id"] = None
                normalized_reaction["name"] = None  # Can get from fan_pages table
                normalized.append(normalized_reaction)
            else:
                normalized.append(reaction)
        return normalized

    def _count_reactions_by_type(
        self, reactions_list: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Count reactions by type from reactions list.

        Args:
            reactions_list: List of reaction dicts with 'type' key

        Returns:
            Dictionary mapping reaction type to count
        """
        counts = {
            "LIKE": 0,
            "LOVE": 0,
            "HAHA": 0,
            "WOW": 0,
            "SAD": 0,
            "ANGRY": 0,
            "CARE": 0,
        }

        for reaction in reactions_list:
            reaction_type = reaction.get("type", "").upper()
            if reaction_type in counts:
                counts[reaction_type] += 1

        return counts
