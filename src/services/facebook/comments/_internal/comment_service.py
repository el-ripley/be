from typing import Any, Dict, Optional

from src.database.postgres.repositories.facebook_queries import (
    create_comment,
    ensure_comment_exists,
    get_comment,
    soft_delete_comment,
    update_comment,
    update_comment_visibility,
)
from src.utils.logger import get_logger

logger = get_logger()


class CommentService:
    """
    Core business logic service for comment operations.
    Handles CRUD operations for comments.
    """

    async def process_comment_by_verb(
        self,
        conn,
        verb: str,
        comment_id: str,
        post_id: str,
        page_id: str,
        parent_id: Optional[str],
        is_from_page: bool,
        facebook_page_scope_user_id: Optional[str],
        message: str,
        photo_url: Optional[str],
        video_url: Optional[str],
        facebook_created_time: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """
        Process a comment based on the verb action.

        Args:
            conn: Database connection
            verb: Action type (add, edited, remove, hide, unhide)
            comment_id: Facebook comment ID
            post_id: Facebook post ID
            page_id: Facebook page ID
            parent_id: Parent comment ID (or None if root comment)
            is_from_page: Whether comment is from page
            facebook_page_scope_user_id: Page scope user ID
            message: Comment message
            photo_url: Photo URL if attachment
            video_url: Video URL if attachment
            facebook_created_time: Facebook creation timestamp

        Returns:
            Processed comment data or None
        """
        try:
            if verb == "add":
                # Create new comment
                await create_comment(
                    conn=conn,
                    comment_id=comment_id,
                    post_id=post_id,
                    fan_page_id=page_id,
                    parent_comment_id=parent_id,
                    is_from_page=is_from_page,
                    facebook_page_scope_user_id=facebook_page_scope_user_id,
                    message=message,
                    photo_url=photo_url,
                    video_url=video_url,
                    facebook_created_time=facebook_created_time,
                )

            elif verb == "edited":
                # Ensure comment exists before updating
                await ensure_comment_exists(
                    conn=conn,
                    comment_id=comment_id,
                    post_id=post_id,
                    fan_page_id=page_id,
                    parent_comment_id=parent_id,
                    is_from_page=is_from_page,
                    facebook_page_scope_user_id=facebook_page_scope_user_id,
                    message=message,
                    photo_url=photo_url,
                    video_url=video_url,
                    facebook_created_time=facebook_created_time,
                )

                # Update existing comment
                await update_comment(
                    conn=conn,
                    comment_id=comment_id,
                    message=message,
                    photo_url=photo_url,
                    video_url=video_url,
                )

            elif verb == "hide":
                # Ensure comment exists before hiding
                await ensure_comment_exists(
                    conn=conn,
                    comment_id=comment_id,
                    post_id=post_id,
                    fan_page_id=page_id,
                    parent_comment_id=parent_id,
                    is_from_page=is_from_page,
                    facebook_page_scope_user_id=facebook_page_scope_user_id,
                    message=message,
                    photo_url=photo_url,
                    video_url=video_url,
                    facebook_created_time=facebook_created_time,
                )

                # Hide comment
                await update_comment_visibility(
                    conn=conn,
                    comment_id=comment_id,
                    is_hidden=True,
                )

            elif verb == "unhide":
                # Ensure comment exists before unhiding
                await ensure_comment_exists(
                    conn=conn,
                    comment_id=comment_id,
                    post_id=post_id,
                    fan_page_id=page_id,
                    parent_comment_id=parent_id,
                    is_from_page=is_from_page,
                    facebook_page_scope_user_id=facebook_page_scope_user_id,
                    message=message,
                    photo_url=photo_url,
                    video_url=video_url,
                    facebook_created_time=facebook_created_time,
                )

                # Unhide comment
                await update_comment_visibility(
                    conn=conn,
                    comment_id=comment_id,
                    is_hidden=False,
                )

            elif verb == "remove":
                # Ensure comment exists before deleting
                existing_comment = await get_comment(conn, comment_id)

                # Soft delete comment
                if existing_comment:
                    await soft_delete_comment(
                        conn=conn,
                        comment_id=comment_id,
                    )

                else:
                    logger.warning(f"⚠️ COMMENT NOT FOUND | ID: {comment_id}")

            else:
                logger.warning(f"⚠️ Unknown comment verb: {verb}")
                return None

            # Get the processed comment data
            comment_data_result = await get_comment(conn, comment_id)

            # Add deletion flag for remove verb
            if verb == "remove" and comment_data_result:
                comment_data_result["deleted"] = True

            return comment_data_result

        except Exception as e:
            logger.error(
                f"❌ Failed to process comment {comment_id} with verb {verb}: {str(e)}"
            )
            raise
