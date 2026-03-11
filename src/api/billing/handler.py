"""
Billing API Handler.
"""

from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import HTTPException

from src.agent.common.constants import OPENAI_MODEL_PRICING
from src.billing.polar.service import PolarService
from src.billing.polar.webhook_handler import PolarWebhookHandler
from src.billing.repositories import billing_queries
from src.billing.sepay.service import SePayService
from src.billing.sepay.webhook_handler import SePayWebhookHandler
from src.billing.stripe.service import StripeService
from src.billing.stripe.webhook_handler import StripeWebhookHandler
from src.database.postgres.connection import async_db_transaction
from src.utils.logger import get_logger

logger = get_logger()


class BillingHandler:
    """Handler for billing API endpoints."""

    def __init__(
        self,
        notification_service: Optional[Any] = None,
    ):
        self.stripe_service = StripeService()
        self.stripe_webhook_handler = StripeWebhookHandler()
        self.sepay_service = SePayService()
        self.sepay_webhook_handler = SePayWebhookHandler()
        self.polar_service = PolarService()
        self.polar_webhook_handler = PolarWebhookHandler()
        if notification_service is not None:
            from src.services.notifications.payment_trigger import (
                PaymentNotificationTrigger,
            )

            self.payment_trigger = PaymentNotificationTrigger(notification_service)
        else:
            self.payment_trigger = None

    async def get_balance(self, user_id: str) -> Dict[str, Any]:
        """Get current credit balance for user."""
        try:
            async with async_db_transaction() as conn:
                balance_record = await billing_queries.get_user_balance_full(
                    conn, user_id
                )

                if not balance_record:
                    return {
                        "balance_usd": Decimal("0"),
                        "lifetime_earned_usd": Decimal("0"),
                        "lifetime_spent_usd": Decimal("0"),
                    }

                return {
                    "balance_usd": Decimal(str(balance_record["balance_usd"])),
                    "lifetime_earned_usd": Decimal(
                        str(balance_record["lifetime_earned_usd"] or 0)
                    ),
                    "lifetime_spent_usd": Decimal(
                        str(balance_record["lifetime_spent_usd"] or 0)
                    ),
                }
        except Exception as e:
            logger.error(f"Error getting balance for user {user_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to get balance")

    async def get_transactions(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> Dict[str, Any]:
        """Get credit transaction history for user."""
        try:
            async with async_db_transaction() as conn:
                transactions = await billing_queries.get_credit_transactions(
                    conn, user_id, limit, offset
                )

                total = await billing_queries.count_credit_transactions(conn, user_id)

                return {
                    "transactions": [
                        {
                            "id": str(t["id"]),
                            "transaction_type": t["transaction_type"],
                            "amount_usd": Decimal(str(t["amount_usd"])),
                            "balance_before_usd": Decimal(str(t["balance_before_usd"])),
                            "balance_after_usd": Decimal(str(t["balance_after_usd"])),
                            "source_type": t["source_type"],
                            "source_id": (
                                str(t["source_id"]) if t["source_id"] else None
                            ),
                            "description": t["description"],
                            "created_at": t["created_at"],
                        }
                        for t in transactions
                    ],
                    "total": total,
                }
        except Exception as e:
            logger.error(f"Error getting transactions for user {user_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to get transactions")

    async def create_topup_checkout(
        self, user_id: str, amount_usd: Decimal, success_url: str, cancel_url: str
    ) -> Dict[str, Any]:
        """Create Stripe Checkout session for top-up."""
        try:
            async with async_db_transaction() as conn:
                checkout_url = await self.stripe_service.create_topup_checkout(
                    conn, user_id, float(amount_usd), success_url, cancel_url
                )
                return {"checkout_url": checkout_url}
        except ValueError as e:
            logger.warning(f"Invalid checkout request: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Error creating topup checkout: {e}")
            raise HTTPException(
                status_code=500, detail="Failed to create checkout session"
            )

    async def create_polar_checkout(
        self, user_id: str, amount_usd: Decimal, success_url: str, cancel_url: str
    ) -> Dict[str, Any]:
        """Create Polar Checkout session for top-up."""
        try:
            async with async_db_transaction() as conn:
                checkout_url = await self.polar_service.create_topup_checkout(
                    conn, user_id, float(amount_usd), success_url, cancel_url
                )
                return {"checkout_url": checkout_url}
        except ValueError as e:
            logger.warning(f"Invalid Polar checkout request: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Error creating Polar checkout: {e}")
            raise HTTPException(
                status_code=500, detail="Failed to create Polar checkout session"
            )

    async def handle_stripe_webhook(self, event: Dict[str, Any]) -> Dict[str, str]:
        """Handle Stripe webhook event."""
        try:
            payment_result = None
            async with async_db_transaction() as conn:
                payment_result = await self.stripe_webhook_handler.handle_event(
                    conn, event
                )
            if self.payment_trigger and payment_result:
                await self.payment_trigger.notify_credits_added(
                    owner_user_id=payment_result["user_id"],
                    amount_usd=payment_result["credits_usd"],
                    source_type="stripe_payment",
                    description=payment_result.get("description"),
                )
            return {"status": "success"}
        except Exception as e:
            logger.error(f"Error handling Stripe webhook: {e}")
            raise HTTPException(status_code=500, detail="Failed to process webhook")

    async def get_models(self) -> Dict[str, Any]:
        """Get list of available OpenAI models with pricing."""
        try:
            models = [
                {
                    "name": name,
                    "input_cost": pricing["input"],
                    "output_cost": pricing["output"],
                    "context_window": pricing["context_window"],
                }
                for name, pricing in OPENAI_MODEL_PRICING.items()
            ]
            return {"models": models}
        except Exception as e:
            logger.error(f"Error getting models: {e}")
            raise HTTPException(status_code=500, detail="Failed to get models")

    async def get_sepay_topup_info(self, user_id: str) -> Dict[str, Any]:
        """Get SePay top-up info for user (config + topup_code)."""
        try:
            async with async_db_transaction() as conn:
                topup_info = await self.sepay_service.get_topup_info(conn, user_id)
                return topup_info
        except Exception as e:
            logger.error(f"Error getting SePay topup info for user {user_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to get topup info")

    async def handle_sepay_webhook(
        self, webhook_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle SePay webhook event."""
        try:
            result = None
            async with async_db_transaction() as conn:
                result = await self.sepay_webhook_handler.handle_webhook(
                    conn, webhook_data
                )
            if (
                self.payment_trigger
                and result
                and result.get("status") == "processed"
                and "user_id" in result
            ):
                await self.payment_trigger.notify_credits_added(
                    owner_user_id=result["user_id"],
                    amount_usd=result["amount_usd"],
                    source_type="sepay_payment",
                    description=result.get("description"),
                )
            return result
        except Exception as e:
            logger.error(f"Error handling SePay webhook: {e}")
            raise HTTPException(status_code=500, detail="Failed to process webhook")

    async def handle_polar_webhook(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Polar webhook event. Always return 200 so Polar stops retrying."""
        try:
            payment_result = None
            async with async_db_transaction() as conn:
                payment_result = await self.polar_webhook_handler.handle_event(
                    conn, event
                )
            if self.payment_trigger and payment_result:
                await self.payment_trigger.notify_credits_added(
                    owner_user_id=payment_result["user_id"],
                    amount_usd=payment_result["credits_usd"],
                    source_type="polar_payment",
                    description=payment_result.get("description"),
                )
            return {"status": "success"}
        except Exception as e:
            logger.error(f"Error handling Polar webhook: {e}")
            # Return 200 so Polar does not retry indefinitely; event is marked failed in DB
            return {"status": "accepted", "processed": False}
