"""
SePay Webhook Handler - Process SePay webhook events.
"""

import re
from decimal import Decimal
from typing import Any, Dict, Optional

import asyncpg

from src.billing.credit_service import add_credits
from src.billing.repositories import billing_queries, sepay_queries
from src.database.postgres.utils import get_current_timestamp
from src.utils.logger import get_logger

logger = get_logger()


class SePayWebhookHandler:
    """Handler for SePay webhook events."""

    async def handle_webhook(
        self, conn: asyncpg.Connection, webhook_data: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Process SePay webhook event.

        Args:
            conn: Database connection
            webhook_data: SePay webhook payload

        Returns:
            Dictionary with status message
        """
        sepay_id = webhook_data.get("id")
        transfer_type = webhook_data.get("transferType")

        # Only process incoming transfers
        if transfer_type != "in":
            transaction_data = {
                "sepay_id": sepay_id,
                "gateway": webhook_data.get("gateway", ""),
                "account_number": webhook_data.get("accountNumber", ""),
                "amount_vnd": webhook_data.get("transferAmount", 0),
                "transfer_type": transfer_type,
                "content": webhook_data.get("content", ""),
                "reference_code": webhook_data.get("referenceCode"),
                "transaction_date": webhook_data.get("transactionDate"),
                "status": "unmatched",
                "event_data": webhook_data,
                "notes": "Outgoing transfer - ignored",
            }
            await sepay_queries.create_sepay_transaction(conn, transaction_data)
            return {"status": "ignored", "message": "Outgoing transfer"}

        # Check idempotency
        if await sepay_queries.check_sepay_transaction_exists(conn, sepay_id):
            return {
                "status": "already_processed",
                "message": "Transaction already processed",
            }

        # Process webhook
        try:
            result = await self._process_transfer(conn, webhook_data)
            # Return full result so BillingHandler can send payment notification (user_id, amount_usd, description)
            return result
        except Exception as e:
            logger.error(f"Error processing SePay webhook {sepay_id}: {e}")
            # Create failed transaction record
            transaction_data = {
                "sepay_id": sepay_id,
                "gateway": webhook_data.get("gateway", ""),
                "account_number": webhook_data.get("accountNumber", ""),
                "amount_vnd": webhook_data.get("transferAmount", 0),
                "transfer_type": transfer_type,
                "content": webhook_data.get("content", ""),
                "reference_code": webhook_data.get("referenceCode"),
                "transaction_date": webhook_data.get("transactionDate"),
                "status": "error",
                "event_data": webhook_data,
                "notes": f"Processing error: {str(e)}",
            }
            await sepay_queries.create_sepay_transaction(conn, transaction_data)
            raise

    async def _process_transfer(
        self, conn: asyncpg.Connection, webhook_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process incoming transfer: match code, validate, add credits."""
        sepay_id = webhook_data.get("id")
        amount_vnd = webhook_data.get("transferAmount", 0)
        content = webhook_data.get("content", "")

        # Get config
        prefix = await sepay_queries.get_sepay_config(conn, "transfer_content_prefix")
        if not prefix:
            prefix = "NAPTIEN"

        min_amount_vnd = await sepay_queries.get_sepay_config(conn, "min_amount_vnd")
        min_amount = int(min_amount_vnd) if min_amount_vnd else 100000

        exchange_rate_str = await sepay_queries.get_sepay_config(
            conn, "exchange_rate_vnd_per_usd"
        )
        exchange_rate = int(exchange_rate_str) if exchange_rate_str else 27500

        # Parse content to extract topup_code
        topup_code = self._extract_topup_code(content, prefix)

        if not topup_code:
            logger.warning(
                f"Could not extract topup_code from content: {content[:100]} (sepay_id={sepay_id})"
            )
            transaction_data = {
                "sepay_id": sepay_id,
                "gateway": webhook_data.get("gateway", ""),
                "account_number": webhook_data.get("accountNumber", ""),
                "amount_vnd": amount_vnd,
                "transfer_type": webhook_data.get("transferType", ""),
                "content": content,
                "reference_code": webhook_data.get("referenceCode"),
                "transaction_date": webhook_data.get("transactionDate"),
                "status": "unmatched",
                "event_data": webhook_data,
                "notes": f"No topup_code found in content (prefix: {prefix})",
            }
            await sepay_queries.create_sepay_transaction(conn, transaction_data)
            return {"status": "unmatched", "message": "No topup_code found"}

        # Find user by topup_code
        user_id = await sepay_queries.get_user_by_topup_code(conn, topup_code)

        if not user_id:
            logger.warning(
                f"Topup code {topup_code} not found for any user (sepay_id={sepay_id})"
            )
            transaction_data = {
                "sepay_id": sepay_id,
                "gateway": webhook_data.get("gateway", ""),
                "account_number": webhook_data.get("accountNumber", ""),
                "amount_vnd": amount_vnd,
                "transfer_type": webhook_data.get("transferType", ""),
                "content": content,
                "reference_code": webhook_data.get("referenceCode"),
                "transaction_date": webhook_data.get("transactionDate"),
                "status": "unmatched",
                "event_data": webhook_data,
                "notes": f"Topup code {topup_code} not found",
            }
            await sepay_queries.create_sepay_transaction(conn, transaction_data)
            return {
                "status": "unmatched",
                "message": f"Topup code {topup_code} not found",
            }

        # Validate amount
        if amount_vnd < min_amount:
            logger.warning(
                f"Amount {amount_vnd} below minimum {min_amount} for user {user_id} (sepay_id={sepay_id})"
            )
            transaction_data = {
                "sepay_id": sepay_id,
                "user_id": user_id,
                "gateway": webhook_data.get("gateway", ""),
                "account_number": webhook_data.get("accountNumber", ""),
                "amount_vnd": amount_vnd,
                "transfer_type": webhook_data.get("transferType", ""),
                "content": content,
                "reference_code": webhook_data.get("referenceCode"),
                "transaction_date": webhook_data.get("transactionDate"),
                "status": "below_minimum",
                "event_data": webhook_data,
                "notes": f"Amount {amount_vnd} < minimum {min_amount}",
            }
            await sepay_queries.create_sepay_transaction(conn, transaction_data)
            return {
                "status": "below_minimum",
                "message": f"Amount {amount_vnd} below minimum {min_amount}",
            }

        # Convert VND to USD
        amount_usd = Decimal(str(amount_vnd)) / Decimal(str(exchange_rate))

        # Capture timestamp before adding credits (for linking credit_transaction later)
        before_timestamp = get_current_timestamp()

        # Add credits
        description = f"Topup via SePay - {amount_vnd:,} VND"
        await add_credits(
            conn=conn,
            user_id=user_id,
            amount=amount_usd,
            source_type="sepay_payment",
            source_id=None,  # Will be set after creating transaction record
            description=description,
        )

        # Create transaction record
        processed_at = get_current_timestamp()
        transaction_data = {
            "sepay_id": sepay_id,
            "user_id": user_id,
            "gateway": webhook_data.get("gateway", ""),
            "account_number": webhook_data.get("accountNumber", ""),
            "amount_vnd": amount_vnd,
            "amount_usd": amount_usd,
            "transfer_type": webhook_data.get("transferType", ""),
            "content": content,
            "reference_code": webhook_data.get("referenceCode"),
            "transaction_date": webhook_data.get("transactionDate"),
            "status": "processed",
            "event_data": webhook_data,
            "notes": f"Successfully processed - {amount_vnd:,} VND = {amount_usd} USD",
            "processed_at": processed_at,
        }
        transaction_id = await sepay_queries.create_sepay_transaction(
            conn, transaction_data
        )

        # Update credit_transaction with sepay_transaction.id as source_id
        await billing_queries.update_credit_transaction_source_id(
            conn=conn,
            user_id=user_id,
            source_type="sepay_payment",
            source_id=transaction_id,
            timestamp_from=before_timestamp,
            timestamp_to=processed_at,
        )

        return {
            "status": "processed",
            "message": f"Added {amount_usd} USD credits",
            "transaction_id": transaction_id,
            "user_id": user_id,
            "amount_usd": amount_usd,
            "description": description,
        }

    def _extract_topup_code(self, content: str, prefix: str) -> Optional[str]:
        """
        Extract topup code from transfer content.

        Examples:
            "DAM QUOC DUNG chuyen tien NAPTIEN ER12ABC Ma giao dich..."
            → Returns "ER12ABC"

        Args:
            content: Transfer content from webhook
            prefix: Prefix to look for (e.g., "NAPTIEN")

        Returns:
            Topup code if found, None otherwise
        """
        if not content:
            return None

        # Convert to uppercase for matching
        content_upper = content.upper()
        prefix_upper = prefix.upper()

        # Pattern: PREFIX followed by whitespace and 6-10 alphanumeric chars
        # Topup code format: ER + 6 chars = 8 chars total, but allow 6-10 for flexibility
        pattern = rf"{re.escape(prefix_upper)}\s+([A-Z0-9]{{6,10}})"

        match = re.search(pattern, content_upper)
        if match:
            return match.group(1)

        return None
