"""
SePay queries - Database operations for SePay integration.
"""

import json
import secrets
from typing import Any, Dict, Optional

import asyncpg

from src.database.postgres.executor import execute_async_returning
from src.database.postgres.utils import generate_uuid, get_current_timestamp


async def get_sepay_config(
    conn: asyncpg.Connection,
    key: str,
) -> Optional[str]:
    """Get SePay config value by key."""
    query = """
        SELECT config_value FROM sepay_config
        WHERE config_key = $1
    """
    result = await conn.fetchval(query, key)
    return result


async def get_all_sepay_config(
    conn: asyncpg.Connection,
) -> Dict[str, str]:
    """Get all SePay config as dictionary."""
    query = """
        SELECT config_key, config_value FROM sepay_config
    """
    rows = await conn.fetch(query)
    return {row["config_key"]: row["config_value"] for row in rows}


async def get_or_create_topup_code(
    conn: asyncpg.Connection,
    user_id: str,
) -> str:
    """Get or create unique topup code for user."""
    # Check if exists
    query = """
        SELECT topup_code FROM user_topup_codes
        WHERE user_id = $1
    """
    existing = await conn.fetchval(query, user_id)

    if existing:
        return existing

    # Generate new code: ER + 6 alphanumeric = "ER12AB3C" (8 chars total)
    max_attempts = 10
    for _ in range(max_attempts):
        code = "ER" + secrets.token_hex(3).upper()  # "ER1A2B3C"

        # Check uniqueness
        check_query = """
            SELECT id FROM user_topup_codes
            WHERE topup_code = $1
        """
        exists = await conn.fetchval(check_query, code)

        if not exists:
            # Insert new code
            code_id = generate_uuid()
            created_at = get_current_timestamp()

            insert_query = """
                INSERT INTO user_topup_codes (id, user_id, topup_code, created_at)
                VALUES ($1, $2, $3, $4)
                RETURNING topup_code
            """
            result = await execute_async_returning(
                conn, insert_query, code_id, user_id, code, created_at
            )
            return result["topup_code"]

    raise ValueError("Failed to generate unique topup code after max attempts")


async def get_user_by_topup_code(
    conn: asyncpg.Connection,
    topup_code: str,
) -> Optional[str]:
    """Get user_id by topup_code."""
    query = """
        SELECT user_id FROM user_topup_codes
        WHERE topup_code = $1
    """
    result = await conn.fetchval(query, topup_code)
    return result


async def check_sepay_transaction_exists(
    conn: asyncpg.Connection,
    sepay_id: int,
) -> bool:
    """Check if SePay transaction already exists (idempotency check)."""
    query = """
        SELECT id FROM sepay_transactions
        WHERE sepay_id = $1
    """
    result = await conn.fetchval(query, sepay_id)
    return result is not None


async def create_sepay_transaction(
    conn: asyncpg.Connection,
    data: Dict[str, Any],
) -> str:
    """Create SePay transaction record."""
    transaction_id = generate_uuid()
    created_at = get_current_timestamp()

    event_data_json = (
        json.dumps(data.get("event_data")) if data.get("event_data") else "{}"
    )

    query = """
        INSERT INTO sepay_transactions
        (id, sepay_id, user_id, gateway, account_number, amount_vnd, amount_usd,
         transfer_type, content, reference_code, transaction_date,
         status, event_data, notes, processed_at, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
        RETURNING id
    """

    result = await execute_async_returning(
        conn,
        query,
        transaction_id,
        data["sepay_id"],
        data.get("user_id"),
        data["gateway"],
        data["account_number"],
        data["amount_vnd"],
        data.get("amount_usd"),
        data["transfer_type"],
        data.get("content"),
        data.get("reference_code"),
        data.get("transaction_date"),
        data["status"],
        event_data_json,
        data.get("notes"),
        data.get("processed_at"),
        created_at,
    )

    return result["id"]


async def get_sepay_transactions_by_user(
    conn: asyncpg.Connection,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[Dict[str, Any]]:
    """Get SePay transaction history for user."""
    query = """
        SELECT * FROM sepay_transactions
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
    """

    rows = await conn.fetch(query, user_id, limit, offset)
    return [dict(row) for row in rows]
