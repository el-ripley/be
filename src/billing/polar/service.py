"""
Polar Service - Checkout session creation for top-up.
"""

import asyncpg

from src.settings import settings
from src.utils.logger import get_logger

logger = get_logger()


class PolarService:
    """Service for Polar checkout operations."""

    def __init__(self) -> None:
        self._access_token = settings.polar_access_token
        self._product_id = settings.polar_product_id
        self._server = (settings.polar_server or "").strip().lower()

    async def create_topup_checkout(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        amount_usd: float,
        success_url: str,
        cancel_url: str,
    ) -> str:
        """
        Create Polar Checkout session for top-up.

        Uses the configured pay-what-you-want product; amount_usd is passed
        as the preset amount (clamped to product min/max).

        Returns:
            checkout_url (str): URL to redirect user to Polar Checkout
        """
        if not self._access_token or not self._product_id:
            raise ValueError("Polar is not configured (POLAR_ACCESS_TOKEN, POLAR_PRODUCT_ID)")

        polar_kw: dict = {"access_token": self._access_token}
        if self._server == "sandbox":
            polar_kw["server"] = "sandbox"

        amount_cents = int(round(amount_usd * 100))
        # Clamp to product limits (min $10 = 1000 cents, max $10000 = 1000000 cents)
        amount_cents = max(1000, min(1_000_000, amount_cents))

        try:
            from polar_sdk import Polar

            with Polar(**polar_kw) as polar:
                request: dict = {
                    "products": [self._product_id],
                    "external_customer_id": user_id,
                    "success_url": success_url,
                    "return_url": cancel_url,
                    "amount": amount_cents,
                }
                checkout = polar.checkouts.create(request=request)
                url = getattr(checkout, "url", None)
                if not url:
                    raise ValueError("Polar checkout did not return a URL")
                return url
        except ImportError as e:
            logger.error(f"polar_sdk not installed: {e}")
            raise ValueError("Polar SDK not available") from e
        except Exception as e:
            logger.error(f"Failed to create Polar checkout: {e}")
            raise
