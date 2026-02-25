"""
Polar Webhook Handler - Process Polar webhook events (order.created, order.paid).
"""

from decimal import Decimal
from typing import Any, Dict, Optional
import asyncpg

from src.billing.repositories import polar_queries
from src.billing.credit_service import add_credits
from src.database.postgres.utils import get_current_timestamp
from src.utils.logger import get_logger

logger = get_logger()


class PolarWebhookHandler:
    """Handler for Polar webhook events."""

    async def handle_event(
        self, conn: asyncpg.Connection, event: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Route event to appropriate handler.

        Returns:
            If credits were added: dict with user_id, credits_usd (Decimal), description.
            Otherwise None.
        """
        event_type = event.get("type") or event.get("event_type")
        event_id = event.get("id")

        if not event_id:
            logger.warning("Polar webhook event missing id")
            return None

        existing_status = await polar_queries.check_polar_webhook_event_processed(
            conn, event_id
        )
        if existing_status == "processed":
            logger.info(
                f"Event {event_id} ({event_type}) already processed - skipping (idempotency)"
            )
            return None

        await polar_queries.upsert_polar_webhook_event(
            conn, event_id, event_type or "unknown", event, status="processing"
        )

        handlers = {
            "order.created": self._handle_order_created,
            "order.paid": self._handle_order_paid,
        }
        handler = handlers.get(event_type)
        if handler:
            try:
                payment_result = await handler(conn, event)
                await polar_queries.upsert_polar_webhook_event(
                    conn, event_id, event_type or "unknown", event, status="processed"
                )
                return payment_result
            except Exception as e:
                logger.error(f"Error handling Polar event {event_type}: {e}")
                await polar_queries.upsert_polar_webhook_event(
                    conn, event_id, event_type or "unknown", event, status="failed"
                )
                raise
        else:
            logger.info(f"Ignoring Polar event type: {event_type}")
            await polar_queries.upsert_polar_webhook_event(
                conn, event_id, event_type or "unknown", event, status="ignored"
            )
            return None

    def _get_order_data(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract order object from event. Supports data.order or data as order."""
        data = event.get("data")
        if not data:
            return None
        if isinstance(data, dict) and "order" in data:
            return data.get("order")
        return data if isinstance(data, dict) else None

    def _get_user_id_from_order(self, order: Dict[str, Any]) -> Optional[str]:
        """Get our user_id from order (customer.external_id)."""
        customer = order.get("customer")
        if isinstance(customer, dict):
            return customer.get("external_id") or customer.get("external_customer_id")
        return None

    def _get_amount_cents_from_order(self, order: Dict[str, Any]) -> Optional[int]:
        """Get order amount in cents for credits: subtotal (before tax), not total.
        Polar: subtotal_amount = before tax; total_amount = after tax. We credit subtotal."""
        subtotal = order.get("subtotal_amount")
        if subtotal is not None:
            return int(subtotal)
        # Fallback for older payloads
        return int(order["amount"]) if order.get("amount") is not None else None

    async def _handle_order_created(
        self, conn: asyncpg.Connection, event: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Create pending polar_payment record for idempotency and tracking."""
        order = self._get_order_data(event)
        if not order:
            logger.warning("order.created: missing data.order")
            return None

        order_id = order.get("id")
        if not order_id:
            logger.warning("order.created: order missing id")
            return None

        existing = await polar_queries.get_polar_payment_by_order_id(conn, str(order_id))
        if existing:
            return None

        user_id = self._get_user_id_from_order(order)
        if not user_id:
            logger.warning("order.created: could not get customer external_id")
            return None

        amount_cents = self._get_amount_cents_from_order(order)
        if amount_cents is None or amount_cents < 0:
            amount_cents = 0
        amount_usd = Decimal(amount_cents) / Decimal(100)

        payment_data = {
            "user_id": user_id,
            "polar_order_id": str(order_id),
            "polar_product_id": str(order["product_id"]) if order.get("product_id") else None,
            "polar_customer_id": None,
            "amount_usd": float(amount_usd),
            "credits_usd": float(amount_usd),
            "currency": order.get("currency") or "usd",
            "status": "pending",
            "billing_reason": order.get("billing_reason"),
            "paid_at": None,
        }
        customer = order.get("customer")
        if isinstance(customer, dict) and customer.get("id"):
            payment_data["polar_customer_id"] = str(customer["id"])

        await polar_queries.create_polar_payment(conn, payment_data)
        logger.info(f"Created polar_payment (pending) for order {order_id}")
        return None

    async def _handle_order_paid(
        self, conn: asyncpg.Connection, event: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update payment to paid and add credits."""
        order = self._get_order_data(event)
        if not order:
            logger.warning("order.paid: missing data.order")
            return None

        order_id = str(order.get("id", ""))
        if not order_id:
            logger.warning("order.paid: order missing id")
            return None

        user_id_from_order = self._get_user_id_from_order(order)
        amount_cents = self._get_amount_cents_from_order(order)

        payment = await polar_queries.get_polar_payment_by_order_id(conn, order_id)
        if not payment:
            # Create pending record if we missed order.created (e.g. webhook order)
            user_id = self._get_user_id_from_order(order)
            amount_cents = self._get_amount_cents_from_order(order) or 0
            amount_usd = Decimal(amount_cents) / Decimal(100)
            if user_id and amount_usd > 0:
                payment_data = {
                    "user_id": user_id,
                    "polar_order_id": order_id,
                    "polar_product_id": str(order["product_id"]) if order.get("product_id") else None,
                    "polar_customer_id": None,
                    "amount_usd": float(amount_usd),
                    "credits_usd": float(amount_usd),
                    "currency": order.get("currency") or "usd",
                    "status": "pending",
                    "billing_reason": order.get("billing_reason"),
                    "paid_at": None,
                }
                await polar_queries.create_polar_payment(conn, payment_data)
                payment = await polar_queries.get_polar_payment_by_order_id(
                    conn, order_id
                )
        if not payment:
            logger.warning(f"order.paid: no polar_payment for order {order_id}")
            return None

        if payment["status"] == "paid":
            logger.info(f"order.paid: already processed for order {order_id}")
            return None

        user_id = payment["user_id"]
        credits_usd = Decimal(str(payment["credits_usd"]))
        paid_at = get_current_timestamp()

        await polar_queries.update_polar_payment_status(
            conn, order_id, "paid", paid_at=paid_at
        )

        payment_id = str(payment["id"])
        description = "Top-up via Polar"
        await add_credits(
            conn=conn,
            user_id=user_id,
            amount=credits_usd,
            source_type="polar_payment",
            source_id=payment_id,
            description=description,
        )
        logger.info(f"Added {credits_usd} credits to user {user_id} (Polar order {order_id})")
        return {
            "user_id": user_id,
            "credits_usd": credits_usd,
            "description": description,
        }
