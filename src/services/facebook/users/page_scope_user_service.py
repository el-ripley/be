from typing import Dict, Any, List, Optional
import json
from src.database.postgres.utils import get_current_timestamp
from src.database.postgres.repositories.facebook_queries import (
    get_facebook_page_scope_user_by_id,
    upsert_facebook_page_scope_user,
    get_facebook_page_scope_users_by_page_ids,
)
from src.database.postgres.connection import async_db_transaction
from src.common.clients.facebook_graph_page_client import FacebookGraphPageClient
from src.services.facebook._core.helpers import execute_graph_client_with_random_tokens
from src.utils.logger import get_logger

logger = get_logger()


class PageScopeUserService:
    """
    Service for managing Facebook page-scoped users.
    """

    def __init__(self):
        pass

    async def get_or_create_page_scope_user(
        self,
        conn,
        psid: str,
        page_id: str,
        page_admins: List[Dict[str, Any]],
        additional_user_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Comprehensive page scope user identity management.

        1. Check if user exists in database first
        2. If not, get page access tokens from facebook_page_admins
        3. Try to fetch user info from Facebook Graph API (with token fallback)
        4. Create user with fetched info (or without if fetch fails)
        5. Return the user record

        Args:
            conn: Database connection
            psid: Page-scoped user ID
            page_id: Facebook page ID
            page_admins: List of page admin records with access tokens
            additional_user_info: Any additional user info from webhook

        Returns:
            User record dictionary or None if creation failed
        """
        try:
            # Step 1: Check if user already exists
            existing_user = await get_facebook_page_scope_user_by_id(conn, psid)

            # Check if existing user needs refresh
            should_refresh = False
            if existing_user:
                # Parse user_info JSON string if it exists
                user_info_str = existing_user.get("user_info", "{}")
                try:
                    user_info = (
                        json.loads(user_info_str)
                        if isinstance(user_info_str, str)
                        else user_info_str
                    )
                except (json.JSONDecodeError, TypeError):
                    user_info = {}

                updated_at = existing_user.get("updated_at", 0)
                current_time = get_current_timestamp()

                # Check if user info is missing important fields
                missing_important_fields = not all(
                    [
                        user_info.get("name"),
                        user_info.get("gender"),
                        user_info.get("profile_pic"),
                    ]
                )

                # Check if data is older than 1 week (7 days = 604800 seconds)
                is_data_stale = (current_time - updated_at) > 604800

                if missing_important_fields or is_data_stale:
                    should_refresh = True
                    logger.info(
                        f"🔄 User {psid} needs refresh - Missing fields: {missing_important_fields}, Stale data: {is_data_stale}"
                    )
                else:
                    logger.debug(f"👤 User already exists with complete info: {psid}")
                    return existing_user

            if not should_refresh:
                logger.info(
                    f"🔍 Creating new page scope user: {psid} for page: {page_id}"
                )

            if not page_admins:
                logger.warning(
                    f"⚠️ No page admins found for page {page_id}, creating user without Facebook info"
                )
                user_info = additional_user_info or {"id": psid}
            else:
                # Step 3: Try to fetch user info from Facebook Graph API
                user_info = await self._fetch_user_info_from_facebook(
                    psid, page_admins, additional_user_info
                )

            # Step 4: Upsert user with the info we have and get the full record
            created_user = await upsert_facebook_page_scope_user(
                conn=conn,
                psid=psid,
                fan_page_id=page_id,
                user_info=user_info,
            )

            action = "refreshed" if should_refresh else "upserted"
            logger.info(
                f"✅ Page scope user {action}: {psid} | Name: {user_info.get('name', 'N/A')}"
            )

            return created_user

        except Exception as e:
            logger.error(f"❌ Failed to ensure page scope user {psid}: {e}")
            return None

    async def _fetch_user_info_from_facebook(
        self,
        psid: str,
        page_admins: List[Dict[str, Any]],
        fallback_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Fetch user info from Facebook Graph API with token fallback.

        Args:
            psid: Page-scoped user ID
            page_admins: List of page admin records with access tokens
            fallback_info: Fallback user info if API calls fail

        Returns:
            User info dictionary
        """
        # Start with fallback info or basic info
        user_info = fallback_info or {"id": psid}

        # Define callback function for getting user info
        async def get_user_info_callback(
            client: FacebookGraphPageClient,
        ) -> Optional[Dict[str, Any]]:
            return await client.get_user_info(psid)

        # Use the token helper for retry logic
        facebook_user_info = await execute_graph_client_with_random_tokens(
            page_admins, get_user_info_callback, f"get user info for {psid}"
        )

        if facebook_user_info:
            # Merge Facebook info with any existing info
            user_info.update(facebook_user_info)
            logger.info(
                f"✅ Successfully fetched Facebook user info for {psid} | Name: {facebook_user_info.get('name', 'N/A')}"
            )
        else:
            logger.warning(
                f"⚠️ Could not fetch Facebook user info for {psid}, using basic info only"
            )

        return user_info

    async def get_page_scope_users_by_page_ids(
        self,
        page_ids: List[str],
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        Get page scope users for given page IDs with pagination.

        Args:
            page_ids: List of Facebook page IDs
            limit: Maximum number of records to return
            offset: Number of records to skip

        Returns:
            Tuple of (list of page scope user records, total count)
        """
        try:
            async with async_db_transaction() as conn:
                users, total = await get_facebook_page_scope_users_by_page_ids(
                    conn, page_ids, limit=limit, offset=offset
                )

                logger.debug(
                    f"🔍 Found {len(users)} page scope users (total: {total}) for {len(page_ids)} pages"
                )

                return users, total
        except Exception as e:
            logger.error(f"❌ Failed to get page scope users by page IDs: {e}")
            return [], 0

    async def get_page_scope_user(
        self,
        conn,
        psid: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get page scope user by PSID, validating it belongs to the specified page.

        Args:
            conn: Database connection
            psid: Page-scoped user ID

        Returns:
            User record if found and belongs to page, None otherwise
        """
        try:
            user = await get_facebook_page_scope_user_by_id(conn, psid)
            if not user:
                return None

            # Validate user belongs to the specified page
            if not user.get("fan_page_id"):
                logger.warning(f"PSID {psid} does not belong to any page")
                return None

            return user
        except Exception as e:
            logger.error(f"❌ Failed to get page scope user {psid}: {e}")
            return None
