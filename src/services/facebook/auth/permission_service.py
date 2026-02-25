from typing import Dict, Any, List, Optional
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.facebook_queries import get_comment
from src.utils.logger import get_logger

logger = get_logger()


class FacebookPermissionService:
    """
    Service for checking user permissions on Facebook pages and comments.
    """

    def __init__(self, page_service):
        """
        Args:
            page_service: FacebookPageService instance
        """
        self.page_service = page_service

    async def check_user_page_admin_permission(
        self, user_id: str, page_id: str
    ) -> bool:
        """
        Check if a user is an admin of a specific Facebook page.

        Args:
            user_id: Internal user ID
            page_id: Facebook page ID

        Returns:
            True if user is admin of the page, False otherwise
        """
        try:
            page_admins = await self.page_service.get_facebook_page_admins_by_user_id(
                user_id
            )

            # Check if user is admin of the specified page
            for admin in page_admins:
                if admin.get("page_id") == page_id:
                    logger.debug(
                        f"✅ User {user_id} is admin of page {page_id} ({admin.get('page_name', 'Unknown')})"
                    )
                    return True

            logger.warning(
                f"❌ User {user_id} is NOT admin of page {page_id}. "
                f"User manages {len(page_admins)} pages: {[admin.get('page_id') for admin in page_admins]}"
            )
            return False

        except Exception as e:
            logger.error(
                f"❌ Failed to check user {user_id} permission for page {page_id}: {e}"
            )
            return False

    async def get_comment_page_info(self, comment_id: str) -> Optional[Dict[str, Any]]:
        """
        Get page information for a specific comment.
        Used for authorization checks.

        Args:
            comment_id: Facebook comment ID

        Returns:
            Dictionary with comment and page info, or None if not found
        """
        try:
            async with async_db_transaction() as conn:
                comment_info = await get_comment(conn, comment_id)

                if comment_info:
                    # Extract page info from the comprehensive comment data
                    page_info = {
                        "comment_id": comment_info["id"],
                        "page_id": comment_info["fan_page_id"],
                        "page_name": comment_info.get("page_name"),
                        "page_avatar": comment_info.get("page_avatar"),
                        "page_category": comment_info.get("page_category"),
                    }

                    logger.debug(
                        f"🔍 Comment {comment_id} belongs to page {page_info['page_id']} ({page_info.get('page_name', 'Unknown')})"
                    )
                    return page_info
                else:
                    logger.warning(
                        f"⚠️ Comment {comment_id} not found or has no page info"
                    )
                    return None

        except Exception as e:
            logger.error(f"❌ Failed to get page info for comment {comment_id}: {e}")
            return None

    async def check_user_comment_permission(
        self, user_id: str, comment_id: str
    ) -> tuple[bool, Optional[str]]:
        """
        Check if a user has permission to manage a specific comment.
        User must be an admin of the page that the comment belongs to.

        Args:
            user_id: Internal user ID
            comment_id: Facebook comment ID

        Returns:
            Tuple of (has_permission: bool, page_id: Optional[str])
        """
        try:
            # Get page info for the comment
            comment_page_info = await self.get_comment_page_info(comment_id)

            if not comment_page_info:
                logger.warning(f"⚠️ Comment {comment_id} not found")
                return False, None

            page_id = comment_page_info["page_id"]

            # Check if user is admin of that page
            has_permission = await self.check_user_page_admin_permission(
                user_id, page_id
            )

            return has_permission, page_id

        except Exception as e:
            logger.error(
                f"❌ Failed to check user {user_id} permission for comment {comment_id}: {e}"
            )
            return False, None

    async def check_user_multiple_comments_permission(
        self, user_id: str, comment_ids: List[str]
    ) -> Dict[str, bool]:
        """
        Check if a user has permission to manage multiple comments.
        Returns a dictionary mapping comment_id to permission status.

        Args:
            user_id: Internal user ID
            comment_ids: List of Facebook comment IDs

        Returns:
            Dictionary mapping comment_id to permission status
        """
        results = {}

        try:
            # Get user's page admin records once for efficiency
            page_admins = await self.page_service.get_facebook_page_admins_by_user_id(
                user_id
            )
            managed_page_ids = {admin.get("page_id") for admin in page_admins}

            logger.debug(
                f"🔍 User {user_id} manages {len(managed_page_ids)} pages: {list(managed_page_ids)}"
            )

            # Check each comment
            async with async_db_transaction() as conn:
                for comment_id in comment_ids:
                    try:
                        comment_info = await get_comment(conn, comment_id)

                        if not comment_info:
                            logger.warning(f"⚠️ Comment {comment_id} not found")
                            results[comment_id] = False
                            continue

                        page_id = comment_info["fan_page_id"]
                        has_permission = page_id in managed_page_ids

                        results[comment_id] = has_permission

                        if not has_permission:
                            logger.warning(
                                f"❌ User {user_id} does NOT have permission for comment {comment_id} "
                                f"on page {page_id} ({comment_info.get('page_name', 'Unknown')})"
                            )

                    except Exception as e:
                        logger.error(
                            f"❌ Failed to check permission for comment {comment_id}: {e}"
                        )
                        results[comment_id] = False

        except Exception as e:
            logger.error(
                f"❌ Failed to check multiple comments permission for user {user_id}: {e}"
            )
            # Mark all as unauthorized if there's an error
            for comment_id in comment_ids:
                results[comment_id] = False

        return results
