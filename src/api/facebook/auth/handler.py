"""
Facebook Handler - Clean production version
Handles Facebook authentication and user management flows
"""

from typing import Tuple, Dict, Any, List, TYPE_CHECKING
import asyncpg

if TYPE_CHECKING:
    from src.database.postgres.entities.user_entities import User

from src.utils.logger import get_logger
from src.common.clients.facebook_graph_client import FacebookGraphClient
from src.services.users.user_service import UserService
from src.services.facebook.auth import FacebookAuthService
from src.services.auth_service import AuthService
from src.settings import settings

logger = get_logger()


class FbHandler:
    """Facebook handler for managing Facebook authentication and user flows"""

    def __init__(
        self,
        user_service: UserService,
        auth_service: FacebookAuthService,
        auth_service_internal: AuthService,
    ):
        self.user_service = user_service
        self.facebook_auth_service = auth_service
        self.auth_service = auth_service_internal

    async def sign_in_facebook(
        self, conn: asyncpg.Connection, fb_code: str
    ) -> Tuple[str, "User", Dict[str, Any], List[Dict[str, Any]], str, str]:
        """
        Sign in with Facebook and create tokens.

        Args:
            conn: Database connection
            fb_code: Facebook authorization code

        Returns:
            Tuple of (internal_user_id, User object, user_info, pages_data, access_token, refresh_token)
        """
        # Create Facebook Graph Client for authentication flow
        graph_client = FacebookGraphClient()

        try:
            # Exchange code for token
            access_token = await graph_client.exchange_code_for_token(
                fb_code,
                settings.fb_app_id,
                settings.fb_app_secret,
                settings.fb_redirect_uri,
            )

            # Get long-lived token directly using exchange_token
            token_result = await graph_client.exchange_token(
                access_token, settings.fb_app_id, settings.fb_app_secret
            )
            long_lived_token = token_result["access_token"]

            # Get user info
            user_info = await graph_client.get_user_info_from_token(long_lived_token)

            # Add access token to user info for return
            user_info["access_token"] = long_lived_token
            facebook_user_id = user_info["id"]  # ASID

            # 1. Ensure user exists and get User object
            internal_user_id, user = (
                await self.user_service.ensure_user_with_facebook_profile(
                    facebook_user_id, user_info
                )
            )

            # 2. Get user's pages for syncing
            pages_data = await graph_client.get_user_pages(long_lived_token)
            pages = pages_data.get("data", [])

            # 3. Sync user's pages using FacebookAuthService
            synced_pages = await self.facebook_auth_service.sync_user_pages(
                facebook_user_id, pages
            )

            # 4. Create token pair using auth service
            access_token, refresh_token = (
                await self.auth_service.create_token_pair_for_user(conn, user)
            )

            return (
                internal_user_id,
                user,
                user_info,
                synced_pages,
                access_token,
                refresh_token,
            )

        except Exception as e:
            logger.error(f"❌ Facebook sign-in failed: {e}")
            raise
