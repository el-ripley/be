"""
Comment Write Service.

Domain logic for creating/updating comments from Facebook data.
Consolidates comment creation logic from CommentSyncService and CommentTreeService.
"""

from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from src.common.clients.facebook_graph_page_client import FacebookGraphPageClient
from src.database.postgres.repositories.facebook_queries import (
    batch_create_comments,
    create_comment,
    get_comment,
)
from src.database.postgres.repositories.facebook_queries.reactions import (
    upsert_comment_reactions,
)
from src.services.facebook._core.helpers import execute_graph_client_with_random_tokens
from src.services.facebook.comments._internal.helpers import get_comment_data
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.utils.logger import get_logger

logger = get_logger()


class CommentWriteService:
    """
    Domain logic for creating/updating comments from Facebook data.

    Handles:
    - Parsing Facebook comment data
    - Creating comment records
    - Fetching and saving reactions
    - Building comment trees from Facebook
    """

    def __init__(self, page_scope_user_service: PageScopeUserService):
        self.page_scope_user_service = page_scope_user_service

    async def create_comment_from_facebook_data(
        self,
        conn,
        page_id: str,
        post_id: str,
        root_comment_id: str,
        fb_comment: Dict[str, Any],
        page_admins: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        Create or update a comment record from Facebook data.

        Consolidated from CommentSyncService._create_comment_from_facebook().

        Args:
            conn: Database connection
            page_id: Facebook page ID
            post_id: Post ID
            root_comment_id: Root comment ID for this tree
            fb_comment: Facebook comment data
            page_admins: List of page admins for token management

        Returns:
            Created comment record or None
        """
        comment_id = fb_comment.get("id")
        if not comment_id:
            return None

        # Parse created_time
        facebook_created_time = None
        if fb_comment.get("created_time"):
            try:
                dt = datetime.fromisoformat(
                    fb_comment["created_time"].replace("Z", "+00:00")
                )
                facebook_created_time = int(dt.timestamp())
            except Exception:
                pass

        # Determine author
        from_data = fb_comment.get("from") or {}
        author_id = from_data.get("id")
        is_from_page = author_id == page_id

        # Get PSID for non-page authors
        psid = None
        if not is_from_page and author_id:
            psid = author_id
            await self.page_scope_user_service.get_or_create_page_scope_user(
                conn=conn,
                psid=psid,
                page_id=page_id,
                page_admins=page_admins,
                additional_user_info={"name": from_data.get("name")},
            )

        # Extract media
        photo_url, video_url = self._extract_attachments(fb_comment)

        # Determine parent (if reply)
        parent_data = fb_comment.get("parent") or {}
        parent_id = parent_data.get("id")

        # Root comment should NEVER have a parent (even if FB API returns one)
        if comment_id == root_comment_id:
            final_parent_id = None
        elif not parent_id:
            # Reply without explicit parent -> parent is root
            final_parent_id = root_comment_id
        else:
            # Reply with explicit parent from FB
            # Only use it if it's not the same as comment_id
            final_parent_id = parent_id if parent_id != comment_id else None

        like_count = fb_comment.get("like_count", 0)

        comment_record = await create_comment(
            conn=conn,
            comment_id=comment_id,
            post_id=post_id,
            fan_page_id=page_id,
            parent_comment_id=final_parent_id,
            is_from_page=is_from_page,
            facebook_page_scope_user_id=psid,
            message=fb_comment.get("message"),
            photo_url=photo_url,
            video_url=video_url,
            facebook_created_time=facebook_created_time,
            like_count=like_count,
            reply_count=fb_comment.get("comment_count", 0),
        )

        # NOTE: Reactions are now fetched in batch after all comments are processed
        # See batch_fetch_and_save_comment_reactions() for better performance
        # Individual fetch is available via save_comment_reactions() for single-comment scenarios

        return comment_record

    async def batch_fetch_and_save_comment_reactions(
        self,
        conn,
        comments: List[Dict[str, Any]],
        post_id: str,
        page_id: str,
        page_admins: List[Dict[str, Any]],
    ) -> None:
        """
        OPTIMIZATION: Batch fetch reactions for multiple comments in a single API call.

        Instead of N API calls (1 per comment), this makes 1 batch API call for up to 50 comments.

        Args:
            conn: Database connection
            comments: List of comment records (with 'id' and 'like_count')
            post_id: Post ID
            page_id: Facebook page ID
            page_admins: List of page admins for token management
        """
        if not comments or not page_admins:
            return

        # Filter comments with reactions (like_count > 0)
        comments_with_reactions = [c for c in comments if c.get("like_count", 0) > 0]

        if not comments_with_reactions:
            return

        # Build batch requests (up to 50 comments per batch)
        batch_requests = []
        comment_ids = []
        for comment in comments_with_reactions:
            comment_id = comment.get("id")
            if comment_id:
                comment_ids.append(comment_id)
                batch_requests.append(
                    {
                        "method": "GET",
                        "relative_url": f"{comment_id}?fields=id,reactions.summary(true).limit(100)",
                    }
                )

        if not batch_requests:
            return

        # Execute batch API call
        async def batch_callback(client: FacebookGraphPageClient):
            return await client.batch_request(batch_requests)

        try:
            batch_results = await execute_graph_client_with_random_tokens(
                page_admins,
                batch_callback,
                f"batch fetch reactions for {len(comment_ids)} comments",
            )

            if not batch_results:
                return

            # Process each result and save reactions
            saved_count = 0
            for i, result in enumerate(batch_results):
                if not result:
                    continue

                comment_id = comment_ids[i]
                reaction_data = result.get("reactions", {})
                reactions_list = reaction_data.get("data", [])

                if not reactions_list:
                    continue

                # Normalize reactions: if reactor_id == page_id, set to None (page reaction)
                normalized_reactions = self._normalize_reactions(
                    reactions_list, page_id
                )

                await upsert_comment_reactions(
                    conn=conn,
                    comment_id=comment_id,
                    post_id=post_id,
                    fan_page_id=page_id,
                    reactions_list=normalized_reactions,
                )
                saved_count += 1

            logger.info(
                f"✅ Batch saved reactions for {saved_count}/{len(comment_ids)} comments"
            )

        except Exception as e:
            logger.warning(f"⚠️ Failed to batch fetch comment reactions: {e}")
            raise

    async def save_comment_reactions(
        self,
        conn,
        comment_id: str,
        post_id: str,
        page_id: str,
        page_admins: List[Dict[str, Any]],
    ) -> None:
        """
        Fetch comment reactions detail from Facebook and save to comment_reactions table.

        DEPRECATED: Use batch_fetch_and_save_comment_reactions for better performance.
        This method is kept for backward compatibility and single-comment scenarios.

        Args:
            conn: Database connection
            comment_id: Comment ID
            post_id: Post ID
            page_id: Facebook page ID
            page_admins: List of page admins for token management
        """
        if not page_admins:
            return

        async def fetch_callback(client: FacebookGraphPageClient):
            return await client.get_comment_reactions(comment_id, limit=100)

        try:
            reactions_data = await execute_graph_client_with_random_tokens(
                page_admins,
                fetch_callback,
                f"fetch comment reactions for {comment_id}",
            )

            if not reactions_data:
                return

            reaction_data = reactions_data.get("reactions", {})
            reactions_list = reaction_data.get("data", [])

            if not reactions_list:
                return

            # Normalize reactions: if reactor_id == page_id, set to None (page reaction)
            normalized_reactions = self._normalize_reactions(reactions_list, page_id)

            await upsert_comment_reactions(
                conn=conn,
                comment_id=comment_id,
                post_id=post_id,
                fan_page_id=page_id,
                reactions_list=normalized_reactions,
            )

            logger.debug(
                f"✅ Saved {len(normalized_reactions)} reactions for comment {comment_id}"
            )
        except Exception as e:
            logger.warning(
                f"⚠️ Failed to fetch comment reactions for {comment_id}: {e}"
            )
            raise

    async def find_root_comment_id(
        self,
        comment_id: str,
        post_id: str,
        page_admins: List[Dict],
    ) -> str:
        """
        Traverse up the comment tree to find the root comment ID.

        From CommentTreeService.find_root_comment_id().

        Args:
            comment_id: Current comment ID
            post_id: Post ID (to identify when we've reached root)
            page_admins: List of page admins for token management

        Returns:
            Root comment ID (the top-level comment whose parent is the post)
        """
        current_comment_id = comment_id
        max_depth = 10  # Safety limit to prevent infinite loops

        for i in range(max_depth):
            comment_data = await get_comment_data(
                comment_id=current_comment_id,
                page_admins=page_admins,
            )

            # Handle case when comment data cannot be retrieved
            if comment_data is None:
                return current_comment_id

            parent = comment_data.get("parent", {})
            parent_id = parent.get("id") if parent else None

            # If no parent or parent is the post, current comment is the root
            if not parent_id or parent_id == post_id:
                return current_comment_id

            # Move up to the parent
            current_comment_id = parent_id

        return current_comment_id

    async def create_comment_tree_from_facebook(
        self,
        conn,
        page_admins: List[Dict[str, Any]],
        root_comment_id: str,
        exclude_comment_id: str,
        page_id: str,
        post_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch full comment tree from Facebook and insert into database.

        From CommentTreeService.fetch_and_create_comment_tree().

        Args:
            conn: Database connection
            page_admins: List of page admins for token management
            root_comment_id: Root comment ID to fetch tree for
            exclude_comment_id: Comment ID to exclude from insertion (the current comment being processed)
            page_id: Facebook page ID
            post_id: Facebook post ID

        Returns:
            List of inserted comment records (as returned from the database) to aid downstream sync.
        """
        try:
            # First fetch the root comment itself
            async def fetch_root_comment_callback(client: FacebookGraphPageClient):
                return await client.fetch_comment_with_parent(root_comment_id)

            root_comment_data = await execute_graph_client_with_random_tokens(
                page_admins,
                fetch_root_comment_callback,
                f"fetch root comment {root_comment_id}",
            )

            if not root_comment_data:
                return []

            # Collect all comments from the tree (flatten nested structure)
            all_comments = []

            # First add the root comment itself
            if root_comment_data and root_comment_data.get("id") != exclude_comment_id:
                # Extract parent comment ID from root comment
                root_parent_id = None
                root_parent_data = root_comment_data.get("parent")
                if root_parent_data:
                    root_parent_id = root_parent_data.get("id")

                root_comment_processed = {
                    "id": root_comment_data.get("id"),
                    "parent_id": root_parent_id,
                    "message": root_comment_data.get("message", ""),
                    "created_time": root_comment_data.get("created_time"),
                    "like_count": root_comment_data.get("like_count", 0),
                    "from_data": root_comment_data.get("from", {}),
                    "attachment": root_comment_data.get("attachment"),
                }
                all_comments.append(root_comment_processed)

            descendants = await self._fetch_descendant_comments(
                page_admins=page_admins,
                root_comment_id=root_comment_id,
                exclude_comment_id=exclude_comment_id,
            )
            all_comments.extend(descendants)

            # Process each comment and prepare for batch insertion
            comments_to_insert = []

            for comment_data in all_comments:
                try:
                    # Extract Facebook user data
                    from_data = comment_data.get("from_data", {})
                    facebook_user_id = from_data.get("id")
                    user_name = from_data.get("name", f"anonymous{facebook_user_id}")

                    # Check if comment is from page itself
                    is_from_page = facebook_user_id == page_id

                    page_scope_user_id = None
                    if not is_from_page:
                        if not facebook_user_id:
                            # Comment from user but no user ID (deleted account, privacy, etc.)
                            # Still insert the comment but with NULL user ID
                            page_scope_user_id = None
                        else:
                            # Ensure page scope user exists (only for user comments, not page comments)
                            page_scope_user = await self.page_scope_user_service.get_or_create_page_scope_user(
                                conn=conn,
                                psid=facebook_user_id,
                                page_id=page_id,
                                page_admins=page_admins,
                                additional_user_info={
                                    "id": facebook_user_id,
                                    "name": user_name,
                                },
                            )
                            page_scope_user_id = (
                                page_scope_user.get("id") if page_scope_user else None
                            )

                    # Extract attachment URLs
                    photo_url, video_url = self._extract_attachments_from_data(
                        comment_data.get("attachment")
                    )

                    # Convert Facebook created_time to timestamp
                    facebook_created_time = None
                    if comment_data.get("created_time"):
                        try:
                            dt = datetime.fromisoformat(
                                comment_data["created_time"].replace("Z", "+00:00")
                            )
                            facebook_created_time = int(dt.timestamp())
                        except (ValueError, AttributeError):
                            logger.warning(
                                f"⚠️ Failed to parse created_time: {comment_data.get('created_time')}"
                            )

                    # Prepare comment for batch insertion
                    comment_to_insert = {
                        "comment_id": comment_data["id"],
                        "post_id": post_id,
                        "fan_page_id": page_id,
                        "parent_comment_id": comment_data.get("parent_id"),
                        "is_from_page": is_from_page,
                        "facebook_page_scope_user_id": page_scope_user_id,
                        "message": comment_data.get("message"),
                        "photo_url": photo_url,
                        "video_url": video_url,
                        "facebook_created_time": facebook_created_time,
                        "like_count": comment_data.get("like_count", 0),
                        "reply_count": comment_data.get("comment_count", 0),
                        "is_hidden": False,
                    }
                    comments_to_insert.append(comment_to_insert)

                except Exception as e:
                    logger.error(
                        f"❌ Failed to process comment {comment_data.get('id')}: {str(e)}"
                    )
                    continue

            inserted_results: List[Dict[str, Any]] = []
            # Batch insert all comments (need to sort by parent relationships)
            if comments_to_insert:
                # Sort comments by dependency order (topological sort)
                # Parents must be inserted before children to avoid FK violations
                inserted_ids = set()
                remaining_comments = comments_to_insert.copy()

                # Keep inserting in waves until all comments are inserted
                max_iterations = len(comments_to_insert) + 1  # Prevent infinite loops
                iteration = 0

                while remaining_comments and iteration < max_iterations:
                    iteration += 1
                    ready_to_insert = []

                    for comment in remaining_comments:
                        parent_id = comment.get("parent_comment_id")

                        # Check if comment is ready to insert:
                        # 1. Has no parent (root comment), OR
                        # 2. Parent was already inserted in previous wave, OR
                        # 3. Parent exists in database (from previous operations)
                        if not parent_id:
                            ready_to_insert.append(comment)
                        elif parent_id in inserted_ids:
                            ready_to_insert.append(comment)
                        else:
                            # Check if parent exists in database
                            existing_parent = await get_comment(conn, parent_id)
                            if existing_parent:
                                ready_to_insert.append(comment)

                    if not ready_to_insert:
                        # No comments can be inserted - circular dependency or missing parent
                        orphaned_comments = [
                            c.get("comment_id") for c in remaining_comments
                        ]
                        logger.error(
                            f"❌ Cannot insert {len(remaining_comments)} comments - missing parents. "
                            f"Orphaned IDs: {orphaned_comments}"
                        )
                        break

                    # Insert this wave of comments
                    inserted_batch = await batch_create_comments(conn, ready_to_insert)
                    inserted_results.extend(inserted_batch)

                    # Track inserted IDs for next iteration
                    for comment in ready_to_insert:
                        inserted_ids.add(comment.get("comment_id"))

                    # Remove inserted comments from remaining list
                    remaining_comments = [
                        c
                        for c in remaining_comments
                        if c.get("comment_id") not in inserted_ids
                    ]

                # Fetch and store reactions for comments with like_count > 0
                await self._fetch_and_store_comment_reactions_batch(
                    conn=conn,
                    page_admins=page_admins,
                    inserted_comments=inserted_results,
                    page_id=page_id,
                    post_id=post_id,
                )

                return inserted_results

            return inserted_results

        except Exception as e:
            logger.error(
                f"❌ Failed to fetch and create comment tree | Root: {root_comment_id} | Error: {str(e)}"
            )
            raise

    async def _fetch_descendant_comments(
        self,
        page_admins: List[Dict[str, Any]],
        root_comment_id: str,
        exclude_comment_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch every descendant comment under the given root comment ID."""
        descendants: List[Dict[str, Any]] = []
        queue: Deque[str] = deque([root_comment_id])
        visited_ids: Set[str] = set()

        while queue:
            current_parent = queue.popleft()
            after_cursor: Optional[str] = None

            while True:
                comments_page = await self._list_comments(
                    page_admins=page_admins,
                    object_id=current_parent,
                    after=after_cursor,
                )

                if not comments_page:
                    break

                comments_batch = comments_page.get("data") or []

                for fb_comment in comments_batch:
                    fb_comment_id = fb_comment.get("id")

                    if not fb_comment_id:
                        continue

                    if fb_comment_id in visited_ids:
                        continue

                    visited_ids.add(fb_comment_id)

                    parent_data = fb_comment.get("parent") or {}
                    parent_id = parent_data.get("id") or current_parent

                    comment_count = fb_comment.get("comment_count", 0)

                    if not (exclude_comment_id and fb_comment_id == exclude_comment_id):
                        descendants.append(
                            {
                                "id": fb_comment_id,
                                "parent_id": parent_id,
                                "message": fb_comment.get("message", ""),
                                "created_time": fb_comment.get("created_time"),
                                "like_count": fb_comment.get("like_count", 0),
                                "from_data": fb_comment.get("from", {}),
                                "attachment": fb_comment.get("attachment"),
                            }
                        )

                    if comment_count and comment_count > 0:
                        queue.append(fb_comment_id)

                after_cursor = (
                    comments_page.get("paging", {}).get("cursors", {}).get("after")
                )

                if not after_cursor:
                    break

        return descendants

    async def _list_comments(
        self,
        page_admins: List[Dict[str, Any]],
        object_id: str,
        after: Optional[str] = None,
        limit: int = 100,
    ) -> Optional[Dict[str, Any]]:
        """List direct comments for a Graph object via the /comments edge."""

        async def list_comments_callback(client: FacebookGraphPageClient):
            return await client.list_comments(
                object_id=object_id,
                limit=limit,
                after=after,
                order="chronological",
            )

        return await execute_graph_client_with_random_tokens(
            page_admins,
            list_comments_callback,
            f"list comments for {object_id}",
        )

    async def _fetch_and_store_comment_reactions_batch(
        self,
        conn,
        page_admins: List[Dict[str, Any]],
        inserted_comments: List[Dict[str, Any]],
        page_id: str,
        post_id: str,
    ):
        """
        Fetch and store reactions for comments that have like_count > 0.

        Args:
            conn: Database connection
            page_admins: List of page admins for token management
            inserted_comments: List of inserted comment records
            page_id: Facebook page ID
            post_id: Facebook post ID
        """
        # Filter comments with like_count > 0
        comments_with_likes = [
            c for c in inserted_comments if c.get("like_count", 0) > 0
        ]

        if not comments_with_likes:
            return

        # Fetch reactions for each comment
        for comment in comments_with_likes:
            comment_id = comment.get("id")
            if not comment_id:
                continue

            try:
                await self.save_comment_reactions(
                    conn=conn,
                    comment_id=comment_id,
                    post_id=post_id,
                    page_id=page_id,
                    page_admins=page_admins,
                )
            except Exception as e:
                logger.warning(
                    f"⚠️ Failed to fetch/store reactions for comment {comment_id}: {str(e)}"
                )
                continue

    def _extract_attachments(
        self, fb_comment: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract photo and video URLs from Facebook comment attachment.

        From CommentSyncService._create_comment_from_facebook().
        """
        photo_url = None
        video_url = None
        attachment = fb_comment.get("attachment") or {}
        att_type = attachment.get("type", "")
        if "photo" in att_type or "sticker" in att_type:
            media = attachment.get("media", {})
            image = media.get("image", {})
            photo_url = image.get("src") or attachment.get("url")
        elif "video" in att_type:
            media = attachment.get("media", {})
            video_url = media.get("source") or attachment.get("url")
        return photo_url, video_url

    def _extract_attachments_from_data(
        self, attachment: Optional[Dict[str, Any]]
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract photo and video URLs from attachment data structure.

        From CommentTreeService.fetch_and_create_comment_tree().
        """
        photo_url = None
        video_url = None
        if attachment:
            attachment_type = attachment.get("type")
            if attachment_type == "photo":
                photo_url = attachment.get("media", {}).get("image", {}).get("src")
            elif attachment_type == "video":
                video_url = attachment.get("url")
            elif attachment_type == "share":
                # Could be either photo or video in shared content
                target = attachment.get("target", {})
                if "photo" in target.get("url", ""):
                    photo_url = target.get("url")
                else:
                    video_url = target.get("url")
        return photo_url, video_url

    def _normalize_reactions(
        self, reactions_list: List[Dict[str, Any]], page_id: str
    ) -> List[Dict[str, Any]]:
        """
        Normalize reactions list: if reactor_id == page_id, set id to None (page reaction).

        Consolidated from both CommentSyncService and CommentTreeService.

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
