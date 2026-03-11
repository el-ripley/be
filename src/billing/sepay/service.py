"""
SePay Service - Get config and manage topup codes.
"""

from typing import Any, Dict

import asyncpg

from src.billing.repositories import sepay_queries
from src.utils.logger import get_logger

logger = get_logger()


class SePayService:
    """Service for SePay operations."""

    async def get_topup_info(
        self, conn: asyncpg.Connection, user_id: str
    ) -> Dict[str, Any]:
        """
        Get topup info for user: config + topup_code.

        Args:
            conn: Database connection
            user_id: User ID

        Returns:
            Dictionary with topup_code, bank info, exchange_rate, limits
        """
        # Get all config
        config = await sepay_queries.get_all_sepay_config(conn)

        # Get or create topup code
        topup_code = await sepay_queries.get_or_create_topup_code(conn, user_id)

        # Build transfer content
        prefix = config.get("transfer_content_prefix", "NAPTIEN")
        transfer_content = f"{prefix} {topup_code}"

        return {
            "topup_code": topup_code,
            "bank_code": config.get("bank_code", "MBBank"),
            "account_number": config.get("account_number", ""),
            "account_name": config.get("account_name", ""),
            "transfer_content": transfer_content,
            "exchange_rate_vnd_per_usd": int(
                config.get("exchange_rate_vnd_per_usd", "27500")
            ),
            "min_amount_vnd": int(config.get("min_amount_vnd", "100000")),
            "max_amount_vnd": int(config.get("max_amount_vnd", "50000000")),
        }
