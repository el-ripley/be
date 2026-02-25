"""
Facebook Post Sync Service.

Syncs Facebook posts metadata for a page into Postgres.
Handles both batch sync and realtime sync operations.
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from src.database.postgres.repositories.facebook_queries.post_sync_states import (
    get_post_sync_state,
    upsert_post_sync_state,
    reset_post_sync_state,
)
from src.database.postgres.repositories.facebook_queries.comments.comment_posts import (
    create_post,
    get_post_by_id,
)
from src.database.postgres.repositories.facebook_queries.reactions import (
    upsert_post_reactions,
)
from src.database.postgres.utils import get_current_timestamp
from src.common.clients.facebook_graph_page_client import FacebookGraphPageClient
from src.services.facebook.auth import FacebookPageService
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.services.facebook._core.helpers import execute_graph_client_with_random_tokens
from src.database.postgres.connection import get_async_connection
from src.utils.logger import get_logger

logger = get_logger()


class PostSyncService:
    """
    Service for syncing Facebook posts metadata for a page.

    Handles:
    - Batch sync: Sync multiple posts from a page
    - Realtime sync: Get or create single post (used by webhooks)
    - Post creation: Create posts from Facebook data
    - Reactions sync: Fetch and save post reactions
    """

    def __init__(
        self,
        page_service: FacebookPageService,
        page_scope_user_service: Optional[PageScopeUserService] = None,
    ) -> None:
        self.page_service = page_service
        self.page_scope_user_service = page_scope_user_service

    async def get_sync_status(
        self,
        conn,
        page_id: str,
    ) -> Dict[str, Any]:
        """
        Get post sync status for a page.

        Returns:
            Dict with posts_sync status info.
        """
        posts_state = await get_post_sync_state(conn, page_id)

        return {
            "fan_page_id": page_id,
            "posts_sync": {
                "status": (posts_state or {}).get("status", "idle"),
                "posts_cursor": (posts_state or {}).get("posts_cursor"),
                "total_synced_posts": (posts_state or {}).get("total_synced_posts", 0),
                "last_sync_at": (posts_state or {}).get("last_sync_at"),
            },
        }

    async def sync_posts(
        self,
        conn,
        page_id: str,
        limit: int = 25,
        continue_from_cursor: bool = True,
        max_concurrent: int = 10,  # Optimized: Lower concurrency reduces contention and improves throughput
    ) -> Dict[str, Any]:
        """
        Sync a batch of posts from a Facebook page.

        Args:
            conn: Database connection
            page_id: Facebook page ID
            limit: Max posts to sync in this batch (1-100)
            continue_from_cursor: Whether to continue from saved cursor
            max_concurrent: Maximum number of posts to process concurrently (default: 10)

        Returns:
            Dict with sync result: synced_posts, has_more, cursor, status
        """
        limit = max(1, min(limit, 100))

        # Get page admins for token
        page_admins = await self.page_service.get_facebook_page_admins_by_page_id(
            conn, page_id
        )
        if not page_admins:
            logger.warning(f"⚠️ No page admins found for page {page_id}")
            return {
                "fan_page_id": page_id,
                "synced_posts": 0,
                "has_more": False,
                "cursor": None,
                "status": "error",
                "error": "no_page_admins",
            }

        # Get current state
        state = await get_post_sync_state(conn, page_id)
        previous_total = int((state or {}).get("total_synced_posts", 0) or 0)
        cursor = (state or {}).get("posts_cursor") if continue_from_cursor else None

        # Fetch posts from Facebook
        posts_data: Optional[List[Dict[str, Any]]] = None
        next_cursor: Optional[str] = None
        cursor_was_reset = False

        for attempt in range(2):
            try:
                posts_data, next_cursor = await self._fetch_posts_batch(
                    conn=conn,
                    page_id=page_id,
                    page_admins=page_admins,
                    limit=limit,
                    after=cursor,
                )
                break
            except Exception as e:
                if attempt == 0 and self._is_cursor_error(e) and cursor:
                    logger.warning(f"⚠️ Cursor expired for page {page_id}, resetting")
                    await reset_post_sync_state(conn, page_id, clear_totals=False)
                    cursor = None
                    cursor_was_reset = True
                    continue
                logger.error(f"❌ Failed to fetch posts for page {page_id}: {e}")
                return {
                    "fan_page_id": page_id,
                    "synced_posts": 0,
                    "has_more": bool(cursor),
                    "cursor": cursor,
                    "status": "error",
                    "error": "fetch_posts_failed",
                }

        if not posts_data:
            await upsert_post_sync_state(
                conn,
                fan_page_id=page_id,
                posts_cursor=None,
                total_synced_posts=previous_total,
                status="completed",
            )
            return {
                "fan_page_id": page_id,
                "synced_posts": 0,
                "has_more": False,
                "cursor": None,
                "status": "completed",
                "cursor_was_reset": cursor_was_reset,
            }

        # Semaphore để giới hạn concurrency và tránh quá tải API/database
        # Lower concurrency reduces contention and improves throughput
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_post(fb_post: Dict[str, Any]) -> Tuple[int, bool]:
            """
            Process a single post.

            Mỗi post được xử lý với connection riêng để tránh conflict
            vì asyncpg không hỗ trợ nhiều queries đồng thời trên cùng một connection.

            Returns:
                Tuple of (success: 1 or 0, is_new: True if new post, False if updated)
            """
            async with semaphore:
                # Mỗi task có connection riêng từ pool để tránh "another operation is in progress"
                async with get_async_connection() as task_conn:
                    try:
                        post_record = await self.create_post_from_facebook_data(
                            conn=task_conn,
                            page_id=page_id,
                            fb_post=fb_post,
                            page_admins=page_admins,
                        )
                        # Check if this is a new post (INSERT) or update (UPDATE)
                        is_new = post_record.get("is_new_post", False)
                        return (1, is_new)
                    except Exception as e:
                        logger.warning(
                            f"⚠️ Failed to sync post {fb_post.get('id')}: {e}"
                        )
                        return (0, False)

        # Process all posts concurrently với error handling
        results = await asyncio.gather(
            *[process_post(fb_post) for fb_post in posts_data], return_exceptions=True
        )

        # Count only NEW posts (INSERT), not updates (UPDATE)
        new_posts_count = 0
        updated_posts_count = 0
        failed_count = 0
        posts_with_reactions = []  # Collect posts that need reactions fetch

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                failed_count += 1
            elif isinstance(r, tuple) and len(r) == 2:
                success, is_new = r
                if success == 1:
                    if is_new:
                        new_posts_count += 1
                    else:
                        updated_posts_count += 1

                    # Check if post has reactions (need to fetch detail)
                    fb_post = posts_data[i]
                    reaction_total = (
                        fb_post.get("reactions", {})
                        .get("summary", {})
                        .get("total_count", 0)
                    )
                    if reaction_total > 0:
                        posts_with_reactions.append(fb_post.get("id"))
                else:
                    failed_count += 1
            else:
                failed_count += 1

        # OPTIMIZATION: Batch fetch all reactions at once instead of individual calls
        if posts_with_reactions:
            try:
                await self._batch_fetch_and_save_post_reactions(
                    conn=conn,
                    post_ids=posts_with_reactions,
                    page_id=page_id,
                    page_admins=page_admins,
                )
            except Exception as e:
                logger.warning(f"⚠️ Failed to batch fetch reactions: {e}")
                # Don't fail sync if reactions fetch fails

        # Update state - only count new posts
        new_total = previous_total + new_posts_count

        # Determine if sync is complete:
        # 1. No more cursor from Facebook - Facebook API says no more posts
        # 2. OR no posts returned AND no cursor - truly no more posts
        # 3. OR all posts in batch are updates (no new posts) - we've already synced all posts
        #    This handles the case where Facebook still returns a cursor but all posts are already in DB
        has_more_from_facebook = bool(next_cursor)
        has_new_posts_in_batch = new_posts_count > 0
        has_posts_in_batch = len(posts_data) > 0

        # Sync is complete if:
        # - No more cursor from Facebook (no cursor = no more posts)
        # - OR we got posts but ALL are updates (no new posts) - meaning we've already synced everything
        #   (If there were new posts, we would have found at least one in this batch)
        is_complete = not has_more_from_facebook or (
            has_posts_in_batch and not has_new_posts_in_batch
        )

        status = "completed" if is_complete else "in_progress"

        # If sync is complete because all posts were updates, clear the cursor
        # to avoid confusion (we've synced everything, no need to continue)
        final_cursor = None if is_complete else next_cursor

        # Log info when sync completes because all posts were updates
        if is_complete and has_posts_in_batch and not has_new_posts_in_batch:
            logger.info(
                f"✅ Page {page_id}: Sync completed. All {len(posts_data)} posts in this batch "
                f"were updates (already synced). No new posts found. Clearing cursor."
            )
        elif (
            has_posts_in_batch and not has_new_posts_in_batch and has_more_from_facebook
        ):
            logger.info(
                f"ℹ️ Page {page_id}: All {len(posts_data)} posts in this batch were updates. "
                f"Continuing with cursor to check for new posts in next batch."
            )

        await upsert_post_sync_state(
            conn,
            fan_page_id=page_id,
            posts_cursor=final_cursor,
            total_synced_posts=new_total,
            status=status,
        )

        logger.info(
            f"✅ Synced {new_posts_count} new posts, {updated_posts_count} updated posts "
            f"for page {page_id} (failed: {failed_count})"
        )

        return {
            "fan_page_id": page_id,
            "synced_posts": new_posts_count,  # Only count new posts
            "updated_posts": updated_posts_count,  # Track updates separately
            "has_more": not is_complete,  # has_more = False when completed
            "cursor": final_cursor,  # Clear cursor when completed
            "status": status,
            "cursor_was_reset": cursor_was_reset,
        }

    async def _fetch_posts_batch(
        self,
        conn,
        page_id: str,
        page_admins: List[Dict[str, Any]],
        limit: int,
        after: Optional[str],
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch a batch of posts from Facebook Graph API."""

        async def fetch_callback(client: FacebookGraphPageClient):
            return await client.list_page_posts(
                page_id=page_id,
                limit=limit,
                after=after,
            )

        result = await execute_graph_client_with_random_tokens(
            page_admins, fetch_callback, f"list posts for page {page_id}"
        )

        if not result:
            return [], None

        data = result.get("data") or []
        paging = result.get("paging") or {}
        cursors = paging.get("cursors") or {}
        next_cursor = cursors.get("after")

        # If Facebook returns empty data but still has a cursor, it's likely a bug or no more posts
        # In this case, we should treat it as no more data (set cursor to None)
        if not data and next_cursor:
            logger.warning(
                f"⚠️ Facebook API returned empty data but has cursor for page {page_id}. "
                f"Treating as no more posts."
            )
            next_cursor = None

        return data, next_cursor

    @staticmethod
    def _is_cursor_error(exc: Exception) -> bool:
        """Detect Facebook cursor-related errors."""
        response = getattr(exc, "response", None)
        if not response:
            return False
        try:
            payload = response.json()
        except Exception:
            return False

        error = (payload or {}).get("error") or {}
        code = error.get("code")
        message = (error.get("message") or "").lower()

        return code in (100, 190) and "cursor" in message

    # ==================== Helper Methods ====================

    def _parse_facebook_post_data(self, fb_post: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse Facebook post data into structured format.

        Args:
            fb_post: Raw Facebook post data from Graph API

        Returns:
            Dictionary with parsed fields:
            - facebook_created_time: Unix timestamp
            - video_link: Video URL if present
            - photo_link: Photo URL if present
        """
        # Parse created_time
        facebook_created_time = None
        if fb_post.get("created_time"):
            try:
                dt = datetime.fromisoformat(
                    fb_post["created_time"].replace("Z", "+00:00")
                )
                facebook_created_time = int(dt.timestamp())
            except Exception as e:
                logger.warning(f"⚠️ Failed to parse created_time: {e}")

        # Extract media from attachments
        video_link = None
        photo_link = None
        attachments = fb_post.get("attachments", {}).get("data", [])
        for att in attachments:
            att_type = att.get("type", "")
            if "video" in att_type and not video_link:
                media = att.get("media", {})
                video_link = media.get("source")
                if not video_link:
                    # Fallback to attachment URL
                    video_link = att.get("url")
            elif "photo" in att_type and not photo_link:
                media = att.get("media", {})
                image = media.get("image", {})
                photo_link = image.get("src")
                if not photo_link:
                    # Fallback to attachment URL
                    photo_link = att.get("url")

        return {
            "facebook_created_time": facebook_created_time,
            "video_link": video_link,
            "photo_link": photo_link,
        }

    def _extract_engagement_counts(self, fb_post: Dict[str, Any]) -> Dict[str, int]:
        """
        Extract engagement counts from Facebook post data.

        Args:
            fb_post: Raw Facebook post data from Graph API

        Returns:
            Dictionary with counts:
            - reaction_total: Total reactions
            - comment_count: Total comments
            - share_count: Total shares
        """
        reactions = fb_post.get("reactions", {}).get("summary", {})
        reaction_total = reactions.get("total_count", 0)
        comments = fb_post.get("comments", {}).get("summary", {})
        comment_count = comments.get("total_count", 0)
        shares = fb_post.get("shares", {})
        share_count = shares.get("count", 0) if shares else 0

        return {
            "reaction_total": reaction_total,
            "comment_count": comment_count,
            "share_count": share_count,
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
            # Convert both to string for comparison (Facebook IDs can be strings or numbers)
            if str(reactor_id) == str(page_id):
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

    # ==================== Realtime Sync Methods ====================

    async def get_or_create_post(
        self,
        conn,
        post_id: str,
        page_id: str,
        page_admins: List[Dict[str, Any]],
        context_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get or create a post by checking database first, then fetching from Facebook Graph API if needed.

        This is used by webhooks and on-demand operations.

        Args:
            conn: Database connection
            post_id: Facebook post ID
            page_id: Facebook page ID
            page_admins: List of page admins for token management
            context_data: Optional context data for logging (e.g., comment_data, message_data)

        Returns:
            Post data dictionary or None if creation fails
        """
        try:
            # Construct full post ID if needed (Facebook Graph API requires format: {page_id}_{post_id})
            full_post_id = post_id
            if "_" not in post_id:
                # If post_id doesn't contain underscore, it's likely a partial ID
                # Construct full format: {page_id}_{post_id}
                full_post_id = f"{page_id}_{post_id}"

            # First check if post already exists in database (try both formats)
            existing_post = await get_post_by_id(conn, post_id)
            if not existing_post and full_post_id != post_id:
                # Also check with full format
                existing_post = await get_post_by_id(conn, full_post_id)

            if existing_post:
                return existing_post

            if not page_admins:
                logger.error(f"❌ No page admins found for page: {page_id}")
                return None

            # Define callback to fetch post content from Facebook Graph API
            async def fetch_post_callback(client: FacebookGraphPageClient):
                return await client.get_post_content(full_post_id)

            # Execute the Facebook API call with token retry logic
            facebook_post_data = await execute_graph_client_with_random_tokens(
                page_admins,
                fetch_post_callback,
                f"fetch post content for {post_id}",
            )

            if not facebook_post_data:
                logger.error(
                    f"❌ Failed to fetch post data from Facebook: {full_post_id}"
                )
                return None

            # Use post ID from Facebook response (may be in full format)
            fb_post_id = facebook_post_data.get("id") or full_post_id
            # For database storage, use the post_id format from Facebook response
            db_post_id = fb_post_id

            # Extract engagement data from Facebook response
            reaction_data = facebook_post_data.get("reactions", {})
            reaction_summary = reaction_data.get("summary", {})
            reaction_total = reaction_summary.get("total_count", 0)
            reactions_list = reaction_data.get("data", [])

            # Check if page reaction exists but not in reactions_list
            # Facebook API may not include page reactions in the list
            # If reaction_total_count > len(reactions_list), there might be a page reaction
            has_page_reaction_in_list = any(
                str(r.get("id")) == str(page_id) for r in reactions_list
            )

            # If total count > list count and no page reaction found, add page reaction
            if reaction_total > len(reactions_list) and not has_page_reaction_in_list:
                # Add page reaction entry (reactor_id will be None after normalization)
                reactions_list.append(
                    {
                        "id": page_id,  # Will be normalized to None
                        "name": None,  # Will be normalized to None
                        "type": "LIKE",  # Default to LIKE (most common)
                    }
                )

            # Count reactions by type
            reaction_counts = self._count_reactions_by_type(reactions_list)

            # Parse post data
            parsed = self._parse_facebook_post_data(facebook_post_data)
            facebook_created_time = parsed["facebook_created_time"]
            video_link = parsed["video_link"]
            photo_link = parsed["photo_link"]

            # Extract engagement counts
            shares_data = facebook_post_data.get("shares", {})
            share_count = shares_data.get("count", 0) if shares_data else 0

            comments_data = facebook_post_data.get("comments", {})
            comment_count = (
                comments_data.get("summary", {}).get("total_count", 0)
                if comments_data
                else 0
            )

            # Create post in database with engagement data
            post_data = await create_post(
                conn=conn,
                post_id=db_post_id,
                fan_page_id=page_id,
                message=facebook_post_data.get("message"),
                video_link=video_link,
                photo_link=photo_link,
                facebook_created_time=facebook_created_time,
                # Engagement fields
                full_picture=facebook_post_data.get("full_picture"),
                permalink_url=facebook_post_data.get("permalink_url"),
                status_type=facebook_post_data.get("status_type"),
                is_published=facebook_post_data.get("is_published", True),
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
                engagement_fetched_at=get_current_timestamp(),
            )

            # Store reactions list to post_reactions table
            if reactions_list:
                # Normalize reactions: if reactor_id == page_id, set to None (page reaction)
                normalized_reactions = self._normalize_reactions(
                    reactions_list, page_id
                )
                await upsert_post_reactions(
                    conn=conn,
                    post_id=db_post_id,
                    fan_page_id=page_id,
                    reactions_list=normalized_reactions,
                )

            return post_data

        except Exception as e:
            logger.error(f"❌ Failed to get or create post {post_id}: {e}")
            if context_data:
                logger.error(f"Context data: {context_data}")
            raise

    async def create_post_from_facebook_data(
        self,
        conn,
        page_id: str,
        fb_post: Dict[str, Any],
        page_admins: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Create or update a post record from Facebook data.

        This is the core method used by sync operations.
        It expects Facebook post data to already be fetched.

        Args:
            conn: Database connection
            page_id: Facebook page ID
            fb_post: Facebook post data from Graph API
            page_admins: List of page admins for token management (for reactions)

        Returns:
            Created post record
        """
        post_id = fb_post.get("id")

        # Parse post data
        parsed = self._parse_facebook_post_data(fb_post)
        facebook_created_time = parsed["facebook_created_time"]
        video_link = parsed["video_link"]
        photo_link = parsed["photo_link"]

        # Extract engagement counts
        engagement = self._extract_engagement_counts(fb_post)
        reaction_total = engagement["reaction_total"]
        comment_count = engagement["comment_count"]
        share_count = engagement["share_count"]

        # Create post record
        post_record = await create_post(
            conn=conn,
            post_id=post_id,
            fan_page_id=page_id,
            message=fb_post.get("message"),
            video_link=video_link,
            photo_link=photo_link,
            facebook_created_time=facebook_created_time,
            full_picture=fb_post.get("full_picture"),
            permalink_url=fb_post.get("permalink_url"),
            status_type=fb_post.get("status_type"),
            is_published=fb_post.get("is_published", True),
            reaction_total_count=reaction_total,
            comment_count=comment_count,
            share_count=share_count,
            engagement_fetched_at=get_current_timestamp(),
        )

        # NOTE: Reactions are now fetched in batch after all posts are processed
        # See _batch_fetch_and_save_post_reactions() for better performance
        # Individual fetch is available via save_post_reactions() for single-post scenarios

        return post_record

    async def _batch_fetch_and_save_post_reactions(
        self,
        conn,
        post_ids: List[str],
        page_id: str,
        page_admins: List[Dict[str, Any]],
    ) -> None:
        """
        OPTIMIZATION: Batch fetch reactions for multiple posts in a single API call.

        Instead of N API calls (1 per post), this makes 1 batch API call for up to 50 posts.

        Args:
            conn: Database connection
            post_ids: List of post IDs to fetch reactions for
            page_id: Facebook page ID
            page_admins: List of page admins for token management
        """
        if not post_ids or not page_admins:
            return

        # Build batch requests (up to 50 posts per batch)
        batch_requests = []
        for post_id in post_ids:
            batch_requests.append(
                {
                    "method": "GET",
                    "relative_url": f"{post_id}?fields=id,reactions.summary(true).limit(100)",
                }
            )

        # Execute batch API call
        async def batch_callback(client: FacebookGraphPageClient):
            return await client.batch_request(batch_requests)

        try:
            batch_results = await execute_graph_client_with_random_tokens(
                page_admins,
                batch_callback,
                f"batch fetch reactions for {len(post_ids)} posts",
            )

            if not batch_results:
                return

            # Process each result and save reactions
            saved_count = 0
            for i, result in enumerate(batch_results):
                if not result:
                    continue

                post_id = post_ids[i]
                reaction_data = result.get("reactions", {})
                reactions_list = reaction_data.get("data", [])

                if not reactions_list:
                    continue

                # Normalize reactions: if reactor_id == page_id, set to None (page reaction)
                normalized_reactions = self._normalize_reactions(
                    reactions_list, page_id
                )

                await upsert_post_reactions(
                    conn=conn,
                    post_id=post_id,
                    fan_page_id=page_id,
                    reactions_list=normalized_reactions,
                )
                saved_count += 1

            logger.info(
                f"✅ Batch saved reactions for {saved_count}/{len(post_ids)} posts"
            )

        except Exception as e:
            logger.warning(f"⚠️ Failed to batch fetch reactions: {e}")
            raise

    async def save_post_reactions(
        self,
        conn,
        post_id: str,
        page_id: str,
        page_admins: List[Dict[str, Any]],
    ) -> None:
        """
        Fetch post reactions detail from Facebook and save to post_reactions table.

        DEPRECATED: Use _batch_fetch_and_save_post_reactions for better performance.
        This method is kept for backward compatibility and single-post scenarios.

        Args:
            conn: Database connection
            post_id: Post ID
            page_id: Facebook page ID
            page_admins: List of page admins for token management
        """
        if not page_admins:
            return

        async def fetch_callback(client: FacebookGraphPageClient):
            return await client.get_post_engagement(post_id, reactions_limit=100)

        try:
            engagement_data = await execute_graph_client_with_random_tokens(
                page_admins,
                fetch_callback,
                f"fetch post reactions for {post_id}",
            )

            if not engagement_data:
                return

            reaction_data = engagement_data.get("reactions", {})
            reactions_list = reaction_data.get("data", [])

            if not reactions_list:
                return

            # Normalize reactions: if reactor_id == page_id, set to None (page reaction)
            normalized_reactions = self._normalize_reactions(reactions_list, page_id)

            await upsert_post_reactions(
                conn=conn,
                post_id=post_id,
                fan_page_id=page_id,
                reactions_list=normalized_reactions,
            )

            logger.debug(
                f"✅ Saved {len(normalized_reactions)} reactions for post {post_id}"
            )
        except Exception as e:
            logger.warning(f"⚠️ Failed to fetch post reactions for {post_id}: {e}")
            raise
