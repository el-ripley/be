from typing import Any, Dict, List

from src.common.clients.facebook_graph_client import FacebookGraphClient
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.facebook_queries import (
    create_facebook_page_admin,
    create_fan_page,
)
from src.utils.logger import get_logger

logger = get_logger()


class FacebookAuthService:
    """
    Service for Facebook authentication and page synchronization.
    """

    def __init__(self):
        pass

    async def sync_user_pages(
        self, facebook_user_id: str, pages_data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Sync user's Facebook pages with explicit checking like your old implementation.
        Continue with other pages if one fails.
        """
        synced_pages = []

        for page_data in pages_data:
            page_id = page_data.get("id")
            page_name = page_data.get("name", "Unknown")

            try:
                clean_page_data = await self._process_facebook_page(
                    facebook_user_id, page_data
                )
                synced_pages.append(clean_page_data)

            except Exception as e:
                logger.error(
                    f"❌ FACEBOOK AUTH SERVICE: Failed to sync page {page_name} ({page_id}): {e}"
                )
                # Continue with other pages
                continue

        return synced_pages

    async def _process_facebook_page(
        self, facebook_user_id: str, page_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process single page following your old pattern."""
        page_id = page_data["id"]
        access_token = page_data.get("access_token", "")
        name = page_data.get("name")
        avatar = page_data.get("picture", {}).get("data", {}).get("url")
        category = page_data.get("category")
        tasks = page_data.get("tasks", [])

        # Extract engagement & stats
        fan_count = page_data.get("fan_count")
        followers_count = page_data.get("followers_count")
        rating_count = page_data.get("rating_count")
        overall_star_rating = page_data.get("overall_star_rating")

        # Extract content & description
        about = page_data.get("about")
        description = page_data.get("description")

        # Extract contact & location
        link = page_data.get("link")
        website = page_data.get("website")
        phone = page_data.get("phone")
        emails = page_data.get("emails")  # Already an array from API
        location = page_data.get("location")  # Already an object from API

        # Extract media
        cover_data = page_data.get("cover")
        cover = cover_data.get("source") if isinstance(cover_data, dict) else cover_data

        # Extract business info
        hours = page_data.get("hours")  # Already an object from API
        is_verified = page_data.get("is_verified")

        async with async_db_transaction() as conn:
            # 1. Get or create fan page, then update fields using upsert
            await create_fan_page(
                conn,
                page_id,
                name=name,
                avatar=avatar,
                category=category,
                fan_count=fan_count,
                followers_count=followers_count,
                rating_count=rating_count,
                overall_star_rating=overall_star_rating,
                about=about,
                description=description,
                link=link,
                website=website,
                phone=phone,
                emails=emails,
                location=location,
                cover=cover,
                hours=hours,
                is_verified=is_verified,
            )

            # 2. Upsert page admin relationship
            await create_facebook_page_admin(
                conn, facebook_user_id, page_id, access_token, tasks
            )

        # 3. Subscribe to webhooks (outside transaction - external API call)
        if access_token:
            try:
                graph_client = FacebookGraphClient()
                await graph_client.subscribe_page_to_webhooks(page_id, access_token)
            except Exception as e:
                logger.error(
                    f"❌ FACEBOOK AUTH SERVICE: Webhook subscription failed for {page_id}: {e}"
                )
                # Don't fail the entire page processing for webhook errors
        else:
            logger.warning(
                f"⚠️ FACEBOOK AUTH SERVICE: No access token for page {page_id}, skipping webhooks"
            )

        clean_page_data = {
            "id": page_id,
            "name": name,
            "avatar": avatar,
            "category": category,
            "fan_count": fan_count,
            "followers_count": followers_count,
            "rating_count": rating_count,
            "overall_star_rating": overall_star_rating,
            "about": about,
            "description": description,
            "link": link,
            "website": website,
            "phone": phone,
            "emails": emails,
            "location": location,
            "cover": cover,
            "hours": hours,
            "is_verified": is_verified,
            "access_token": access_token,
            "tasks": tasks,
            "raw_data": page_data,
        }

        return clean_page_data
