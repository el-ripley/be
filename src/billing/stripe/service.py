"""
Stripe Service - Customer and checkout session management.
"""

import os
from typing import Optional
import asyncpg
import stripe

from src.billing.repositories import stripe_queries
from src.utils.logger import get_logger

logger = get_logger()

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")


class StripeService:
    """Service for Stripe operations."""

    async def _get_user_email(
        self, conn: asyncpg.Connection, user_id: str
    ) -> Optional[str]:
        """
        Get user email from facebook_app_scope_users table.

        Returns:
            Email if found, None otherwise
        """
        try:
            query = """
                SELECT email FROM facebook_app_scope_users
                WHERE user_id = $1 AND email IS NOT NULL
                LIMIT 1
            """
            result = await conn.fetchval(query, user_id)
            return result
        except Exception as e:
            logger.warning(f"Failed to get user email for {user_id}: {e}")
            return None

    async def get_or_create_customer(
        self, conn: asyncpg.Connection, user_id: str, email: Optional[str] = None
    ) -> tuple[str, Optional[str]]:
        """
        Get or create Stripe customer for user.

        If email is not provided, tries to get it from database.

        Returns:
            tuple: (stripe_customer_id, email)
        """
        # Get email from database if not provided
        if not email:
            email = await self._get_user_email(conn, user_id)

        # Check if customer already exists
        existing = await stripe_queries.get_stripe_customer_by_user(conn, user_id)
        if existing:
            # Update email if we have it and customer doesn't have one
            if email and not existing.get("email"):
                try:
                    stripe.Customer.modify(
                        existing["stripe_customer_id"],
                        email=email,
                    )
                    # Update in database
                    await stripe_queries.get_or_create_stripe_customer(
                        conn,
                        user_id=user_id,
                        stripe_customer_id=existing["stripe_customer_id"],
                        email=email,
                    )
                except Exception as e:
                    logger.warning(f"Failed to update customer email: {e}")

            return existing["stripe_customer_id"], email or existing.get("email")

        # Create new Stripe customer
        try:
            customer = stripe.Customer.create(
                email=email,
                metadata={"user_id": user_id},
            )

            # Save to database
            await stripe_queries.get_or_create_stripe_customer(
                conn,
                user_id=user_id,
                stripe_customer_id=customer.id,
                email=email,
            )

            return customer.id, email
        except Exception as e:
            logger.error(f"Failed to create Stripe customer: {e}")
            raise

    async def create_topup_checkout(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        amount_usd: float,
        success_url: str,
        cancel_url: str,
    ) -> str:
        """
        Create Stripe Checkout session for top-up.

        Args:
            amount_usd: Top-up amount in USD (e.g., 5.00, 10.00, 20.00)

        Returns:
            checkout_url (str): URL to redirect user to Stripe Checkout
        """
        # Get Stripe product from database (always returns custom product)
        product = await stripe_queries.get_stripe_product_by_amount(conn, amount_usd)
        if not product:
            raise ValueError("Stripe product not found")

        # Determine actual amount and credits
        # Custom product always uses requested amount
        actual_amount = amount_usd
        actual_credits = amount_usd

        # Get Stripe product ID and price ID from database
        stripe_product_id = product.get("stripe_product_id")
        stripe_price_id = product.get("stripe_price_id")

        if not stripe_product_id or not stripe_price_id:
            raise ValueError(
                "Stripe product ID or price ID not configured for top-up product"
            )

        # Get or create customer (with email if available)
        customer_id, customer_email = await self.get_or_create_customer(conn, user_id)

        # Create checkout session
        try:
            # For all top-ups (fixed or custom), use price_data with the Stripe product
            # This allows dynamic amounts while using the correct Stripe product
            line_items = [
                {
                    "price_data": {
                        "currency": "usd",
                        "product": stripe_product_id,  # Use the dedicated top-up product
                        "unit_amount": int(actual_amount * 100),  # Convert to cents
                    },
                    "quantity": 1,
                }
            ]

            session_params = {
                "customer": customer_id,
                "mode": "payment",
                "line_items": line_items,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "metadata": {
                    "user_id": user_id,
                    "product_code": product["product_code"],
                    "product_id": str(product["id"]),
                    "amount_usd": str(actual_amount),
                    "credits_usd": str(actual_credits),
                },
            }

            # Note: Cannot pass both "customer" and "customer_email" at the same time
            # If customer already exists but doesn't have email, let Stripe collect it
            if not customer_email:
                # Fallback: let Stripe collect email if needed (for SCA, receipts, etc.)
                session_params["customer_email_collection"] = "if_required"

            session = stripe.checkout.Session.create(**session_params)

            return session.url
        except Exception as e:
            logger.error(f"Failed to create topup checkout: {e}")
            raise
