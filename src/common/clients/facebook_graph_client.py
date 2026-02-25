from typing import Dict, Any
from src.common.clients.http_client import HttpClient
from src.utils import get_logger

logger = get_logger()


class FacebookGraphClient:
    def __init__(self, api_version: str = "v23.0"):
        """
        Initialize the FacebookGraphClient.

        Args:
            api_version: Facebook Graph API version to use
        """
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self.http = HttpClient()

    # ========================================================================
    # AUTHENTICATION & TOKEN MANAGEMENT
    # ========================================================================

    async def exchange_code_for_token(
        self,
        fb_code: str,
        app_id: str,
        app_secret: str,
        redirect_uri: str,
    ) -> str:
        """
        Exchange Facebook authorization code for access token.

        Args:
            fb_code: Facebook authorization code
            app_id: Facebook App ID
            app_secret: Facebook App Secret
            redirect_uri: Redirect URI for OAuth flow

        Returns:
            Access token string
        """

        url = f"{self.base_url}/oauth/access_token"
        params = {
            "client_id": app_id,
            "client_secret": app_secret,
            "redirect_uri": redirect_uri,
            "code": fb_code,
        }

        response = await self.http.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        return data["access_token"]

    async def exchange_token(
        self,
        short_lived_token: str,
        app_id: str,
        app_secret: str,
    ) -> Dict[str, Any]:
        """
        Exchange a short-lived token for a long-lived token.

        Args:
            short_lived_token: Short-lived access token
            app_id: Facebook App ID
            app_secret: Facebook App Secret

        Returns:
            Long-lived token data with 'access_token' and optional 'expires_in'
        """

        url = f"{self.base_url}/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_lived_token,
        }
        response = await self.http.get(url, params=params)
        response.raise_for_status()
        result = response.json()

        return result

    async def get_user_info_from_token(self, access_token: str) -> Dict[str, Any]:
        """
        Get user info from Facebook using access token.

        Args:
            access_token: Facebook access token

        Returns:
            User data dictionary
        """

        url = f"{self.base_url}/me"
        params = {
            "fields": "id,name,email,picture,locale,timezone,verified",
            "access_token": access_token,
        }

        response = await self.http.get(url, params=params)
        response.raise_for_status()
        user_data = response.json()

        return user_data

    # ========================================================================
    # PAGE MANAGEMENT
    # ========================================================================

    async def get_user_pages(
        self,
        user_access_token: str,
    ) -> Dict[str, Any]:
        """
        Get pages managed by a user.

        Args:
            user_access_token: User's access token

        Returns:
            List of pages
        """
        url = f"{self.base_url}/me/accounts"
        params = {
            "fields": "id,name,access_token,category,picture,fan_count,about,description,followers_count,link,location,phone,website,emails,cover,hours,is_verified,rating_count,overall_star_rating",
            "access_token": user_access_token,
        }
        response = await self.http.get(url, params=params)
        response.raise_for_status()
        pages_data = response.json()

        return pages_data

    async def subscribe_page_to_webhooks(
        self,
        page_id: str,
        page_access_token: str,
    ) -> Dict[str, Any]:
        """
        Subscribe a page to webhook events.

        Args:
            page_id: Facebook Page ID
            page_access_token: Page access token for subscription

        Returns:
            Subscription response
        """
        url = f"{self.base_url}/{page_id}/subscribed_apps"
        subscribed_fields = [
            # Core messaging events
            "messages",
            "messaging_postbacks",
            "messaging_optins",
            "message_deliveries",
            "message_reads",
            "message_echoes",
            "message_reactions",  # Valid subscription field
            # Advanced messaging events
            "messaging_handovers",
            "messaging_referrals",
            "messaging_account_linking",
            "messaging_policy_enforcement",
            "standby",
            # Page-level events
            "feed",
            "mention",
            "name",
            "picture",
            "conversations",
        ]
        data = {
            "subscribed_fields": ",".join(subscribed_fields),
            "access_token": page_access_token,
        }
        response = await self.http.post(url, data=data)
        response.raise_for_status()
        return response.json()
