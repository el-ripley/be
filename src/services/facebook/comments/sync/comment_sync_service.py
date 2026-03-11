"""
Facebook Comment Sync Service.

Syncs Facebook comment trees for posts into Postgres.
"""

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from src.common.clients.facebook_graph_page_client import FacebookGraphPageClient
from src.database.postgres.connection import get_async_connection
from src.database.postgres.repositories.facebook_queries.post_comment_sync_states import (
    get_comment_sync_state,
    reset_comment_sync_state,
    upsert_comment_sync_state,
)
from src.services.facebook._core.helpers import execute_graph_client_with_random_tokens
from src.services.facebook.auth import FacebookPageService
from src.services.facebook.comments.comment_conversation_service import (
    CommentConversationService,
)
from src.services.facebook.comments.sync.comment_write_service import (
    CommentWriteService,
)
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.utils.logger import get_logger

logger = get_logger()


class CommentSyncService:
    """
    Service for batch syncing Facebook comment trees for posts.

    Syncs comment trees (root comments + all nested replies) for a specific post.
    """

    def __init__(
        self,
        page_service: FacebookPageService,
        page_scope_user_service: PageScopeUserService,
        comment_conversation_service: CommentConversationService = None,
        comment_write_service: CommentWriteService = None,
    ) -> None:
        self.page_service = page_service
        self.page_scope_user_service = page_scope_user_service
        self.comment_conversation_service = (
            comment_conversation_service or CommentConversationService()
        )
        self.comment_write_service = comment_write_service or CommentWriteService(
            page_scope_user_service
        )

    async def get_post_comment_sync_status(
        self,
        conn,
        post_id: str,
    ) -> Dict[str, Any]:
        """Get comment sync status for a specific post."""
        state = await get_comment_sync_state(conn, post_id)
        if not state:
            return {
                "post_id": post_id,
                "status": "idle",
                "comments_cursor": None,
                "total_synced_root_comments": 0,
                "total_synced_comments": 0,
                "last_sync_at": None,
            }
        # Return consistent structure (exclude extra DB fields)
        return {
            "post_id": state.get("post_id"),
            "status": state.get("status", "idle"),
            "comments_cursor": state.get("comments_cursor"),
            "total_synced_root_comments": state.get("total_synced_root_comments", 0),
            "total_synced_comments": state.get("total_synced_comments", 0),
            "last_sync_at": state.get("last_sync_at"),
        }

    async def sync_comments(
        self,
        conn,
        page_id: str,
        post_id: str,
        limit: int = 10,
        continue_from_cursor: bool = True,
        max_concurrent: int = 10,  # Optimized: Lower concurrency reduces API rate limiting and DB contention
    ) -> Dict[str, Any]:
        """
        Sync comment trees for a specific post.

        This fetches N root comments and ALL their nested replies,
        building complete comment trees.

        Args:
            conn: Database connection
            page_id: Facebook page ID
            post_id: Post ID to sync comments for
            limit: Max ROOT comment trees per batch (1-50)
            continue_from_cursor: Whether to continue from saved cursor
            max_concurrent: Maximum number of comment trees to process concurrently (default: 5)

        Returns:
            Dict with sync result
        """
        limit = max(1, min(limit, 50))

        # Get page admins
        page_admins = await self.page_service.get_facebook_page_admins_by_page_id(
            conn, page_id
        )
        if not page_admins:
            return {
                "fan_page_id": page_id,
                "post_id": post_id,
                "synced_root_comments": 0,
                "synced_total_comments": 0,
                "has_more": False,
                "cursor": None,
                "status": "error",
                "error": "no_page_admins",
            }

        # Get current state
        state = await get_comment_sync_state(conn, post_id)
        prev_root = int((state or {}).get("total_synced_root_comments", 0) or 0)
        prev_total = int((state or {}).get("total_synced_comments", 0) or 0)
        cursor = (state or {}).get("comments_cursor") if continue_from_cursor else None

        # Fetch root comments
        root_comments: List[Dict[str, Any]] = []
        next_cursor: Optional[str] = None
        cursor_was_reset = False

        for attempt in range(2):
            try:
                root_comments, next_cursor = await self._fetch_root_comments_batch(
                    page_admins=page_admins,
                    post_id=post_id,
                    limit=limit,
                    after=cursor,
                )
                break
            except Exception as e:
                if attempt == 0 and self._is_cursor_error(e) and cursor:
                    logger.warning(f"⚠️ Cursor expired for post {post_id}, resetting")
                    await reset_comment_sync_state(conn, post_id, clear_totals=False)
                    cursor = None
                    cursor_was_reset = True
                    continue
                logger.error(f"❌ Failed to fetch comments for post {post_id}: {e}")
                return {
                    "fan_page_id": page_id,
                    "post_id": post_id,
                    "synced_root_comments": 0,
                    "synced_total_comments": 0,
                    "has_more": bool(cursor),
                    "cursor": cursor,
                    "status": "error",
                    "error": "fetch_comments_failed",
                }

        if not root_comments:
            await upsert_comment_sync_state(
                conn,
                post_id=post_id,
                fan_page_id=page_id,
                comments_cursor=None,
                total_synced_root_comments=prev_root,
                total_synced_comments=prev_total,
                status="completed",
            )
            return {
                "fan_page_id": page_id,
                "post_id": post_id,
                "synced_root_comments": 0,
                "synced_total_comments": 0,
                "has_more": False,
                "cursor": None,
                "status": "completed",
                "cursor_was_reset": cursor_was_reset,
            }

        # Semaphore để giới hạn concurrency và tránh quá tải API/database
        # Lower concurrency reduces API rate limiting and DB contention
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_root_comment(root_comment: Dict[str, Any]) -> Tuple[int, int]:
            """
            Process a root comment tree.

            Mỗi comment tree được xử lý với connection riêng để tránh conflict
            vì asyncpg không hỗ trợ nhiều queries đồng thời trên cùng một connection.

            Returns:
                Tuple of (synced_root, synced_total) - (1, count) if success, (0, 0) if failed
            """
            async with semaphore:
                # Mỗi task có connection riêng từ pool để tránh "another operation is in progress"
                async with get_async_connection() as task_conn:
                    try:
                        tree_count = await self._sync_comment_tree(
                            conn=task_conn,
                            page_id=page_id,
                            post_id=post_id,
                            root_comment=root_comment,
                            page_admins=page_admins,
                        )
                        return (1, tree_count)
                    except Exception as e:
                        logger.warning(
                            f"⚠️ Failed to sync comment tree {root_comment.get('id')}: {e}"
                        )
                        return (0, 0)

        # Process all root comments concurrently với error handling
        results = await asyncio.gather(
            *[process_root_comment(root_comment) for root_comment in root_comments],
            return_exceptions=True,
        )

        # Aggregate results
        synced_root = 0
        synced_total = 0

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"❌ Unexpected error processing comment tree: {result}")
                continue

            root_count, total_count = result
            synced_root += root_count
            synced_total += total_count

        # Update state
        new_root = prev_root + synced_root
        new_total = prev_total + synced_total
        has_more = bool(next_cursor)
        status = "in_progress" if has_more else "completed"

        await upsert_comment_sync_state(
            conn,
            post_id=post_id,
            fan_page_id=page_id,
            comments_cursor=next_cursor,
            total_synced_root_comments=new_root,
            total_synced_comments=new_total,
            status=status,
        )

        logger.info(
            f"✅ Synced {synced_root} root comments ({synced_total} total) for post {post_id}"
        )

        return {
            "fan_page_id": page_id,
            "post_id": post_id,
            "synced_root_comments": synced_root,
            "synced_total_comments": synced_total,
            "has_more": has_more,
            "cursor": next_cursor,
            "status": status,
            "cursor_was_reset": cursor_was_reset,
        }

    async def _fetch_root_comments_batch(
        self,
        page_admins: List[Dict[str, Any]],
        post_id: str,
        limit: int,
        after: Optional[str],
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch root comments (direct comments on post)."""

        async def fetch_callback(client: FacebookGraphPageClient):
            return await client.list_comments(
                object_id=post_id,
                limit=limit,
                after=after,
                order="reverse_chronological",
                filter_stream=False,  # Only fetch toplevel/root comments, not all nested
            )

        result = await execute_graph_client_with_random_tokens(
            page_admins, fetch_callback, f"list root comments for post {post_id}"
        )

        if not result:
            return [], None

        data = result.get("data") or []
        paging = result.get("paging") or {}
        cursors = paging.get("cursors") or {}
        next_cursor = cursors.get("after")

        return data, next_cursor

    async def _sync_comment_tree(
        self,
        conn,
        page_id: str,
        post_id: str,
        root_comment: Dict[str, Any],
        page_admins: List[Dict[str, Any]],
    ) -> int:
        """
        Sync a complete comment tree (root + all nested replies).

        Returns total number of comments synced (including root).
        """
        root_id = root_comment.get("id")

        # Safety check: verify this is actually a root comment (no parent or parent is post)
        parent_data = root_comment.get("parent") or {}
        parent_id = parent_data.get("id")
        if parent_id and parent_id != post_id:
            logger.warning(
                f"⚠️ Skipping non-root comment {root_id} (has parent: {parent_id})"
            )
            return 0

        all_fb_comments = [root_comment]

        # Recursively fetch all replies
        replies = await self._fetch_all_replies_recursive(
            page_admins=page_admins,
            comment_id=root_id,
        )
        all_fb_comments.extend(replies)

        # Create comment records and collect results
        synced = 0
        comment_records = []

        for fb_comment in all_fb_comments:
            comment_record = (
                await self.comment_write_service.create_comment_from_facebook_data(
                    conn=conn,
                    page_id=page_id,
                    post_id=post_id,
                    root_comment_id=root_id,
                    fb_comment=fb_comment,
                    page_admins=page_admins,
                )
            )
            if comment_record:
                synced += 1
                comment_records.append(comment_record)

        # OPTIMIZATION: Batch fetch reactions for all comments at once
        if comment_records:
            try:
                await self.comment_write_service.batch_fetch_and_save_comment_reactions(
                    conn=conn,
                    comments=comment_records,
                    post_id=post_id,
                    page_id=page_id,
                    page_admins=page_admins,
                )
            except Exception as e:
                logger.warning(f"⚠️ Failed to batch fetch comment reactions: {e}")
                # Don't fail sync if reactions fetch fails

        # Delegate conversation sync to CommentConversationService
        if comment_records:
            await self.comment_conversation_service.sync_backfill_comments_to_conversation(
                conn=conn,
                fan_page_id=page_id,
                post_id=post_id,
                root_comment_id=root_id,
                comments=comment_records,
            )

        return synced

    async def _fetch_all_replies_recursive(
        self,
        page_admins: List[Dict[str, Any]],
        comment_id: str,
        max_depth: int = 10,
        current_depth: int = 0,
    ) -> List[Dict[str, Any]]:
        """Recursively fetch all replies to a comment."""
        if current_depth >= max_depth:
            return []

        all_replies = []
        cursor = None

        while True:

            async def fetch_callback(client: FacebookGraphPageClient):
                return await client.list_comments(
                    object_id=comment_id,
                    limit=100,
                    after=cursor,
                    order="chronological",
                )

            try:
                result = await execute_graph_client_with_random_tokens(
                    page_admins, fetch_callback, f"fetch replies for {comment_id}"
                )
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch replies for {comment_id}: {e}")
                break

            if not result:
                break

            batch = result.get("data") or []
            all_replies.extend(batch)

            # Recursively get replies to replies
            for reply in batch:
                nested = await self._fetch_all_replies_recursive(
                    page_admins=page_admins,
                    comment_id=reply.get("id"),
                    max_depth=max_depth,
                    current_depth=current_depth + 1,
                )
                all_replies.extend(nested)

            # Check for more pages
            paging = result.get("paging") or {}
            cursor = paging.get("cursors", {}).get("after")
            if not cursor:
                break

        return all_replies

    # ========================================================================
    # HELPERS
    # ========================================================================

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
