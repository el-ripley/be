"""
Billing queries - Database operations for credit balance and transactions.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional

import asyncpg

from src.database.postgres.executor import execute_async_returning, execute_async_single
from src.database.postgres.utils import generate_uuid, get_current_timestamp


async def get_or_create_user_credit_balance(
    conn: asyncpg.Connection,
    user_id: str,
    initial_balance: Decimal = Decimal("3.0"),
) -> Dict[str, Any]:
    """Get or create user credit balance record."""
    # Check if exists
    query = """
        SELECT * FROM user_credit_balance
        WHERE user_id = $1
    """
    existing = await execute_async_single(conn, query, user_id)

    if existing:
        return dict(existing)

    # Create new balance record
    balance_id = generate_uuid()
    created_at = get_current_timestamp()

    insert_query = """
        INSERT INTO user_credit_balance 
        (id, user_id, balance_usd, lifetime_earned_usd, lifetime_spent_usd, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
    """

    result = await execute_async_returning(
        conn,
        insert_query,
        balance_id,
        user_id,
        initial_balance,
        initial_balance,  # lifetime_earned_usd
        Decimal("0"),  # lifetime_spent_usd
        created_at,
        created_at,
    )

    return dict(result)


async def get_user_balance(
    conn: asyncpg.Connection,
    user_id: str,
) -> Decimal:
    """Get current credit balance for user."""
    query = """
        SELECT balance_usd FROM user_credit_balance
        WHERE user_id = $1
    """
    result = await conn.fetchval(query, user_id)

    if result is None:
        # Balance doesn't exist, create it with default
        balance_record = await get_or_create_user_credit_balance(conn, user_id)
        return Decimal(str(balance_record["balance_usd"]))

    return Decimal(str(result))


async def get_billing_setting(
    conn: asyncpg.Connection,
    key: str,
) -> Decimal:
    """Get billing setting value by key."""
    query = """
        SELECT setting_value FROM billing_settings
        WHERE setting_key = $1
    """
    result = await conn.fetchval(query, key)

    if result is None:
        raise ValueError(f"Billing setting '{key}' not found")

    return Decimal(str(result))


async def update_user_balance(
    conn: asyncpg.Connection,
    user_id: str,
    new_balance: Decimal,
    last_spent_at: Optional[int] = None,
    last_credited_at: Optional[int] = None,
) -> None:
    """Update user credit balance."""
    updated_at = get_current_timestamp()

    # Build update query dynamically based on what fields to update
    updates = ["balance_usd = $2", "updated_at = $3"]
    params = [user_id, new_balance, updated_at]
    param_index = 4

    if last_spent_at is not None:
        updates.append(f"last_spent_at = ${param_index}")
        params.append(last_spent_at)
        param_index += 1

    if last_credited_at is not None:
        updates.append(f"last_credited_at = ${param_index}")
        params.append(last_credited_at)
        param_index += 1

    query = f"""
        UPDATE user_credit_balance
        SET {', '.join(updates)}
        WHERE user_id = $1
    """

    await conn.execute(query, *params)


async def create_credit_transaction(
    conn: asyncpg.Connection,
    user_id: str,
    transaction_type: str,
    amount: Decimal,
    balance_before: Decimal,
    balance_after: Decimal,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Create a credit transaction record."""
    transaction_id = generate_uuid()
    created_at = get_current_timestamp()

    import json

    metadata_json = json.dumps(metadata) if metadata else None

    query = """
        INSERT INTO credit_transactions
        (id, user_id, transaction_type, amount_usd, balance_before_usd, balance_after_usd,
         source_type, source_id, description, metadata, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        RETURNING id
    """

    result = await execute_async_returning(
        conn,
        query,
        transaction_id,
        user_id,
        transaction_type,
        amount,
        balance_before,
        balance_after,
        source_type,
        source_id,
        description,
        metadata_json,
        created_at,
    )

    return result["id"]


async def get_credit_transactions(
    conn: asyncpg.Connection,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Get credit transaction history for user."""
    query = """
        SELECT * FROM credit_transactions
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
    """

    rows = await conn.fetch(query, user_id, limit, offset)
    return [dict(row) for row in rows]


async def update_lifetime_stats(
    conn: asyncpg.Connection,
    user_id: str,
    earned_delta: Optional[Decimal] = None,
    spent_delta: Optional[Decimal] = None,
) -> None:
    """Update lifetime earned/spent stats (for analytics only)."""
    updates = ["updated_at = $2"]
    params = [user_id, get_current_timestamp()]
    param_index = 3

    if earned_delta is not None:
        updates.append(f"lifetime_earned_usd = lifetime_earned_usd + ${param_index}")
        params.append(earned_delta)
        param_index += 1

    if spent_delta is not None:
        updates.append(f"lifetime_spent_usd = lifetime_spent_usd + ${param_index}")
        params.append(spent_delta)
        param_index += 1

    query = f"""
        UPDATE user_credit_balance
        SET {', '.join(updates)}
        WHERE user_id = $1
    """

    await conn.execute(query, *params)


async def get_user_balance_full(
    conn: asyncpg.Connection,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Get full user credit balance record including lifetime stats."""
    query = """
        SELECT balance_usd, lifetime_earned_usd, lifetime_spent_usd
        FROM user_credit_balance
        WHERE user_id = $1
    """
    result = await conn.fetchrow(query, user_id)
    return dict(result) if result else None


async def count_credit_transactions(
    conn: asyncpg.Connection,
    user_id: str,
) -> int:
    """Count total credit transactions for user."""
    query = """
        SELECT COUNT(*) FROM credit_transactions
        WHERE user_id = $1
    """
    result = await conn.fetchval(query, user_id)
    return result or 0


async def update_credit_transaction_source_id(
    conn: asyncpg.Connection,
    user_id: str,
    source_type: str,
    source_id: str,
    timestamp_from: int,
    timestamp_to: int,
) -> None:
    """Update credit_transaction source_id for a recently created transaction."""
    update_query = """
        UPDATE credit_transactions
        SET source_id = $1
        WHERE id = (
            SELECT id FROM credit_transactions
            WHERE user_id = $2
                AND source_type = $3
                AND source_id IS NULL
                AND created_at >= $4
                AND created_at <= $5
            ORDER BY created_at DESC
            LIMIT 1
        )
    """
    await conn.execute(
        update_query, source_id, user_id, source_type, timestamp_from, timestamp_to
    )
