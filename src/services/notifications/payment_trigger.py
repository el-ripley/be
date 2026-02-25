"""
Payment notification trigger: when credits are added (Stripe or SePay),
create an in-app notification for the user.
"""

from decimal import Decimal
from typing import Optional

from src.services.notifications.notification_service import NotificationService
from src.utils.logger import get_logger

logger = get_logger()

TYPE_CREDITS_ADDED = "payment.credits_added"
REFERENCE_TYPE_CREDIT_TRANSACTION = "credit_transaction"


class PaymentNotificationTrigger:
    """Creates notifications when credits are added to a user's account."""

    def __init__(self, notification_service: NotificationService):
        self.notification_service = notification_service

    async def notify_credits_added(
        self,
        owner_user_id: str,
        amount_usd: Decimal,
        source_type: str,
        description: Optional[str] = None,
    ) -> None:
        """
        Create a notification when credits have been added (e.g. after Stripe/SePay payment).
        Call this after the payment transaction has committed.
        """
        amount_str = f"${amount_usd:.2f}"
        title = f"Credits added: {amount_str}"
        metadata = {
            "amount_usd": float(amount_usd),
            "source_type": source_type,
        }
        try:
            await self.notification_service.create(
                owner_user_id=owner_user_id,
                type=TYPE_CREDITS_ADDED,
                title=title,
                body=description,
                reference_type=REFERENCE_TYPE_CREDIT_TRANSACTION,
                reference_id=None,
                metadata=metadata,
            )
        except Exception as e:
            logger.warning(
                "Payment notification trigger failed for user %s: %s",
                owner_user_id,
                e,
                exc_info=True,
            )
