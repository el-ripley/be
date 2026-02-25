from typing import Dict, Any, List, Optional
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.facebook_queries import (
    get_page_by_id,
    get_facebook_page_admins_by_user_id,
    get_facebook_page_admins_by_page,
)
from src.utils.logger import get_logger

logger = get_logger()


class FacebookPageService:
    """
    Service for Facebook page management and admin operations.
    """

    def __init__(self):
        pass

    async def get_page_by_id(self, conn, page_id: str) -> Optional[Dict[str, Any]]:
        """Get or create a Facebook page."""
        return await get_page_by_id(conn, page_id)

    async def get_facebook_page_admins_by_user_id(
        self, user_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all Facebook page admin records for a specific user.
        Used for authorization checks.

        Args:
            user_id: Internal user ID

        Returns:
            List of page admin records with page information
        """
        try:
            async with async_db_transaction() as conn:
                page_admins = await get_facebook_page_admins_by_user_id(conn, user_id)

                logger.debug(
                    f"🔍 Found {len(page_admins)} page admin records for user {user_id}"
                )

                return page_admins

        except Exception as e:
            logger.error(f"❌ Failed to get page admins for user {user_id}: {e}")
            return []

    async def get_facebook_page_admins_by_page_id(
        self, conn, page_id: str
    ) -> List[Dict[str, Any]]:
        """Get all page admins for a Facebook page.
        will implement redis cache here later for better performance
        """
        return await get_facebook_page_admins_by_page(conn, page_id)

    async def get_facebook_page_admins_by_user_id_with_conn(
        self, conn, user_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all Facebook page admin records for a specific user using provided connection.

        Args:
            conn: Database connection
            user_id: Internal user ID

        Returns:
            List of page admin records with page information
        """
        try:
            page_admins = await get_facebook_page_admins_by_user_id(conn, user_id)
            logger.debug(
                f"🔍 Found {len(page_admins)} page admin records for user {user_id}"
            )
            return page_admins
        except Exception as e:
            logger.error(f"❌ Failed to get page admins for user {user_id}: {e}")
            return []
