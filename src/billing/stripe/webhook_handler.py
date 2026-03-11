"""
Stripe Webhook Handler - Process Stripe webhook events.
"""

from decimal import Decimal
from typing import Any, Dict, Optional

import asyncpg

from src.billing.credit_service import add_credits
from src.billing.repositories import stripe_queries
from src.database.postgres.utils import get_current_timestamp
from src.utils.logger import get_logger

logger = get_logger()


class StripeWebhookHandler:
    """Handler for Stripe webhook events."""

    async def handle_event(
        self, conn: asyncpg.Connection, event: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Route event to appropriate handler.

        Args:
            conn: Database connection
            event: Stripe event dictionary

        Returns:
            If credits were added: dict with user_id, credits_usd (Decimal), description.
            Otherwise None.
        """
        event_type = event.get("type")
        event_id = event.get("id")

        # Check if event was already processed (idempotency)
        # This handles Stripe retries after DB drops or server restarts
        existing_status = await stripe_queries.check_webhook_event_processed(
            conn, event_id
        )

        if existing_status == "processed":
            logger.info(
                f"Event {event_id} ({event_type}) already processed - skipping (idempotency)"
            )
            return None

        # Log webhook event for idempotency
        await stripe_queries.upsert_webhook_event(
            conn, event_id, event_type, event, status="processing"
        )

        # Route to handler
        handlers = {
            "checkout.session.completed": self._handle_checkout_completed,
        }

        handler = handlers.get(event_type)
        if handler:
            try:
                payment_result = await handler(conn, event["data"]["object"])
                # Mark as processed
                await stripe_queries.upsert_webhook_event(
                    conn, event_id, event_type, event, status="processed"
                )
                return payment_result
            except Exception as e:
                logger.error(f"Error handling Stripe event {event_type}: {e}")
                # Mark as failed
                await stripe_queries.upsert_webhook_event(
                    conn, event_id, event_type, event, status="failed"
                )
                raise
        else:
            logger.info(f"Ignoring Stripe event type: {event_type}")
            # Mark as ignored
            await stripe_queries.upsert_webhook_event(
                conn, event_id, event_type, event, status="ignored"
            )
            return None

    async def _handle_checkout_completed(
        self, conn: asyncpg.Connection, session: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Handle successful checkout - add credits for top-up. Returns payment result for notification."""
        metadata = session.get("metadata", {})
        user_id = metadata.get("user_id")
        mode = session.get("mode")

        if not user_id:
            logger.warning("Checkout session missing user_id in metadata")
            return None

        if mode != "payment":
            logger.warning(
                f"Unsupported checkout mode: {mode}. Only 'payment' mode is supported."
            )
            return None

        # Top-up checkout
        logger.info(f"Processing top-up checkout for user {user_id}")
        product_code = metadata.get("product_code")
        product_id = metadata.get("product_id")
        amount_usd = metadata.get("amount_usd")
        credits_usd = metadata.get("credits_usd")

        # Get customer
        customer = await stripe_queries.get_stripe_customer_by_user(conn, user_id)
        if not customer:
            logger.warning(
                f"Stripe customer not found for user {user_id} - "
                f"likely retry event after DB drop or customer was deleted. Skipping."
            )
            return None

        # Create payment record
        payment_data = {
            "user_id": user_id,
            "stripe_customer_id": customer["id"],
            "stripe_payment_intent_id": session.get("payment_intent"),
            "stripe_product_id": product_id,
            "amount_usd": float(amount_usd),
            "credits_usd": float(credits_usd),
            "status": "succeeded",
            "paid_at": get_current_timestamp(),
        }
        payment_id = await stripe_queries.create_stripe_payment(conn, payment_data)
        logger.info(f"Created stripe_payment record: {payment_id}")

        # Add credits
        credits_amount = Decimal(str(credits_usd))
        description = f"Top-up - {product_code}"
        await add_credits(
            conn=conn,
            user_id=user_id,
            amount=credits_amount,
            source_type="stripe_payment",
            source_id=payment_id,
            description=description,
        )
        logger.info(f"Added {credits_amount} credits to user {user_id}")
        return {
            "user_id": user_id,
            "credits_usd": credits_amount,
            "description": description,
        }
