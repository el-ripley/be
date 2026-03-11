"""
Facebook Full Sync Service.

Orchestrates full sync process for posts and comments.
Syncs ALL posts first, then syncs comments for newest posts without comments.
"""

from typing import Any, Dict

from src.database.postgres.repositories.facebook_queries.post_comment_sync_states import (
    get_posts_needing_comment_sync,
)
from src.services.facebook.comments.sync.comment_sync_service import CommentSyncService
from src.services.facebook.posts.post_sync_service import PostSyncService
from src.utils.logger import get_logger

logger = get_logger()


class FullSyncService:
    """
    Service for orchestrating full sync of posts and comments for a page.

    Flow:
    1. Sync ALL posts (loop until done) - uses PostSyncService
    2. Get N newest posts without comments
    3. Sync comments for each post (20 root comments per post) - uses CommentSyncService
    """

    def __init__(
        self,
        post_sync_service: PostSyncService,
        comment_sync_service: CommentSyncService,
    ) -> None:
        self.post_sync_service = post_sync_service
        self.comment_sync_service = comment_sync_service

    async def full_sync(
        self,
        conn,
        page_id: str,
        posts_limit: int = 50,
        comments_per_post: int = 20,
    ) -> Dict[str, Any]:
        """
        Perform full sync for a page: sync all posts, then sync comments for newest posts.

        Args:
            conn: Database connection
            page_id: Facebook page ID
            posts_limit: Max posts to sync comments for (default: 50)
            comments_per_post: Max root comments per post (default: 20)

        Returns:
            Dict with sync result
        """
        total_posts_synced = 0
        total_comments_synced = 0
        error_occurred = None

        try:
            # Phase 1: Sync ALL posts (loop until done)
            logger.info(
                f"🔄 Starting full sync for page {page_id} - Phase 1: Sync posts"
            )

            posts_synced_count = 0
            while True:
                result = await self.post_sync_service.sync_posts(
                    conn=conn,
                    page_id=page_id,
                    limit=100,  # Max batch size for posts
                    continue_from_cursor=True,
                )

                if result.get("status") == "error":
                    error_occurred = result.get("error", "unknown_error")
                    logger.error(
                        f"❌ Failed to sync posts for page {page_id}: {error_occurred}"
                    )
                    break

                synced = result.get("synced_posts", 0)
                posts_synced_count += synced
                # Get total from state (sync_posts updates state, so query it)
                from src.database.postgres.repositories.facebook_queries.post_sync_states import (
                    get_post_sync_state,
                )

                state = await get_post_sync_state(conn, page_id)
                total_posts_synced = (
                    (state or {}).get("total_synced_posts", 0)
                    if state
                    else posts_synced_count
                )

                has_more = result.get("has_more", False)
                if not has_more:
                    logger.info(
                        f"✅ Completed posts sync for page {page_id}: {posts_synced_count} posts"
                    )
                    break

            if error_occurred:
                return {
                    "fan_page_id": page_id,
                    "status": "error",
                    "error": error_occurred,
                    "total_posts_synced": 0,
                    "total_comments_synced": 0,
                }

            # Phase 2: Sync comments for newest posts without comments
            logger.info(f"🔄 Phase 2: Sync comments for {posts_limit} newest posts")

            posts_needing_sync = await get_posts_needing_comment_sync(
                conn=conn,
                fan_page_id=page_id,
                limit=posts_limit,
            )

            if not posts_needing_sync:
                logger.info(f"✅ No posts need comment sync for page {page_id}")
                return {
                    "fan_page_id": page_id,
                    "status": "completed",
                    "total_posts_synced": total_posts_synced,
                    "total_comments_synced": 0,
                    "posts_with_comments": 0,
                }

            # Sync comments for each post
            posts_with_comments = 0
            total_root_comments = 0
            total_all_comments = 0

            for i, post in enumerate(posts_needing_sync):
                post_id = post.get("id")
                if not post_id:
                    continue

                try:
                    result = await self.comment_sync_service.sync_comments(
                        conn=conn,
                        page_id=page_id,
                        post_id=post_id,
                        limit=comments_per_post,
                        continue_from_cursor=True,
                    )

                    if result.get("status") != "error":
                        posts_with_comments += 1
                        total_root_comments += result.get("synced_root_comments", 0)
                        total_all_comments += result.get("synced_total_comments", 0)
                    else:
                        logger.warning(
                            f"⚠️ Failed to sync comments for post {post_id}: {result.get('error')}"
                        )

                except Exception as e:
                    logger.error(f"❌ Error syncing comments for post {post_id}: {e}")
                    continue

            total_comments_synced = total_all_comments

            logger.info(
                f"✅ Completed full sync for page {page_id}: "
                f"{posts_with_comments} posts with comments, "
                f"{total_comments_synced} total comments"
            )

            return {
                "fan_page_id": page_id,
                "status": "completed",
                "total_posts_synced": total_posts_synced,
                "total_comments_synced": total_comments_synced,
                "posts_with_comments": posts_with_comments,
                "total_root_comments": total_root_comments,
            }

        except Exception as e:
            logger.error(f"❌ Error during full sync for page {page_id}: {e}")
            return {
                "fan_page_id": page_id,
                "status": "error",
                "error": str(e),
                "total_posts_synced": total_posts_synced,
                "total_comments_synced": total_comments_synced,
            }

    async def get_sync_status(
        self,
        conn,
        page_id: str,
    ) -> Dict[str, Any]:
        """
        Get full sync status for a page.

        Returns:
            Dict with sync status info

        Note:
            - If posts_sync.status == "completed", the page has been synced (even if total_synced_posts == 0)
            - FE should check posts_sync.status or overall_status, not just total_synced_posts
        """
        # Get posts sync status
        posts_status = await self.post_sync_service.get_sync_status(
            conn=conn, page_id=page_id
        )

        posts_sync_data = posts_status.get("posts_sync", {})
        posts_sync_status = posts_sync_data.get("status", "idle")
        last_sync_at = posts_sync_data.get("last_sync_at")

        # Get posts needing comment sync
        posts_needing_sync = await get_posts_needing_comment_sync(
            conn=conn,
            fan_page_id=page_id,
            limit=1,  # Just to check if any exist
        )

        has_posts_needing_sync = len(posts_needing_sync) > 0

        # Determine if sync has been completed
        # A page is considered "completed" if:
        # 1. Posts sync status is "completed" (even if total_synced_posts == 0 for empty pages)
        # 2. AND no posts need comment sync
        is_posts_sync_completed = posts_sync_status == "completed"
        is_comments_sync_completed = not has_posts_needing_sync
        overall_completed = is_posts_sync_completed and is_comments_sync_completed

        return {
            "fan_page_id": page_id,
            "posts_sync": posts_sync_data,
            "comments_sync": {
                "status": "completed" if is_comments_sync_completed else "pending",
                "has_posts_needing_sync": has_posts_needing_sync,
            },
            "overall_status": "completed" if overall_completed else "pending",
            # Add helper field to make it clear if page needs initial sync
            # This helps FE distinguish between "never synced" vs "synced but empty"
            "needs_initial_sync": (
                posts_sync_status == "idle" and last_sync_at is None
            ),
        }
