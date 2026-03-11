"""
Credit Service - High-level operations for credit balance management.
"""

from decimal import Decimal
from typing import Optional

import asyncpg

from src.billing.repositories import billing_queries
from src.database.postgres.utils import get_current_timestamp


async def initialize_user_credits(
    conn: asyncpg.Connection,
    user_id: str,
    amount: Decimal = Decimal("3.0"),
) -> None:
    """Initialize user credit balance with free credits (called on registration)."""
    # Check if balance already exists
    balance_query = """
        SELECT balance_usd FROM user_credit_balance
        WHERE user_id = $1
    """
    existing_balance = await conn.fetchval(balance_query, user_id)

    if existing_balance is not None:
        # Balance already exists, don't overwrite
        return

    # Create new balance record with initial credits
    _ = await billing_queries.get_or_create_user_credit_balance(conn, user_id, amount)

    # Create transaction record for initial credits
    await billing_queries.create_credit_transaction(
        conn=conn,
        user_id=user_id,
        transaction_type="welcome_bonus",
        amount=amount,
        balance_before=Decimal("0"),
        balance_after=amount,
        source_type="system",
        source_id=None,
        description="Welcome bonus - Initial free credits",
        metadata={"type": "welcome_bonus"},
    )


async def get_balance(
    conn: asyncpg.Connection,
    user_id: str,
) -> Decimal:
    """Get current credit balance for user."""
    return await billing_queries.get_user_balance(conn, user_id)


async def can_use_ai(
    conn: asyncpg.Connection,
    user_id: str,
) -> bool:
    """Check if user has enough balance to use AI (above min_balance_usd)."""
    balance = await get_balance(conn, user_id)
    min_balance_usd = await billing_queries.get_billing_setting(conn, "min_balance_usd")

    # User can use AI if balance >= min_balance_usd (balance > 0)
    return balance > min_balance_usd


async def deduct_credits_for_agent(
    conn: asyncpg.Connection,
    user_id: str,
    agent_response_id: str,
    raw_cost: Decimal,
    model: str,
) -> None:
    """
    Deduct credits after agent completes.

    Args:
        conn: Database connection
        user_id: User ID
        agent_response_id: Agent response ID (source_id for transaction)
        raw_cost: Raw cost from OpenAI (before multiplier)
        model: Model name used
    """
    # Get charge multiplier
    multiplier = await billing_queries.get_billing_setting(conn, "charge_multiplier")
    charged_cost = raw_cost * multiplier

    # Get current balance
    balance_before = await get_balance(conn, user_id)
    balance_after = balance_before - charged_cost

    # Update balance
    await billing_queries.update_user_balance(
        conn, user_id, balance_after, last_spent_at=get_current_timestamp()
    )

    # Update lifetime stats
    await billing_queries.update_lifetime_stats(conn, user_id, spent_delta=charged_cost)

    # Record transaction
    await billing_queries.create_credit_transaction(
        conn=conn,
        user_id=user_id,
        transaction_type="ai_usage",
        amount=-charged_cost,  # Negative = debit
        balance_before=balance_before,
        balance_after=balance_after,
        source_type="agent_response",
        source_id=agent_response_id,
        description=f"AI usage - {model}",
        metadata={
            "raw_cost": str(raw_cost),
            "multiplier": str(multiplier),
            "model": model,
        },
    )


async def deduct_credits_after_agent(
    conn: asyncpg.Connection,
    agent_response_id: str,
) -> None:
    """
    Deduct credits after agent finalization.
    Queries agent_response data and deducts credits if there's a cost.

    Args:
        conn: Database connection
        agent_response_id: Agent response ID
    """
    # Get agent_response data
    query = """
        SELECT user_id, total_cost, model
        FROM agent_response
        WHERE id = $1
    """
    agent_data = await conn.fetchrow(query, agent_response_id)

    if not agent_data:
        return

    # Deduct if there's a cost (all requests now use system API key)
    if agent_data["total_cost"] and agent_data["total_cost"] > 0:
        await deduct_credits_for_agent(
            conn=conn,
            user_id=agent_data["user_id"],
            agent_response_id=agent_response_id,
            raw_cost=Decimal(str(agent_data["total_cost"])),
            model=agent_data["model"] or "unknown",
        )


async def add_credits(
    conn: asyncpg.Connection,
    user_id: str,
    amount: Decimal,
    source_type: str,
    source_id: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    """
    Add credits from top-up.

    Args:
        conn: Database connection
        user_id: User ID
        amount: Amount to add (positive)
        source_type: Type of source (e.g., "stripe_payment")
        source_id: ID of the source record (e.g., stripe_payments.id)
        description: Optional description
    """
    # Get current balance
    balance_before = await get_balance(conn, user_id)
    balance_after = balance_before + amount

    # Update balance
    await billing_queries.update_user_balance(
        conn, user_id, balance_after, last_credited_at=get_current_timestamp()
    )

    # Update lifetime stats
    await billing_queries.update_lifetime_stats(conn, user_id, earned_delta=amount)

    # Determine transaction type based on source_type and description
    if source_type == "stripe_payment" or "topup" in (description or "").lower():
        # Top-up payments from Stripe
        transaction_type = "topup"
    else:
        # Other adjustments (admin, system, etc.)
        transaction_type = "adjustment"

    # Record transaction
    await billing_queries.create_credit_transaction(
        conn=conn,
        user_id=user_id,
        transaction_type=transaction_type,
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_after,
        source_type=source_type,
        source_id=source_id,
        description=description,
        metadata=None,
    )


async def admin_adjust_credits(
    conn: asyncpg.Connection,
    user_id: str,
    amount: Decimal,  # positive = add, negative = deduct
    admin_id: str,
    reason: str,
) -> str:
    """
    Admin-only credit adjustment.

    This function allows admins to manually adjust user credits.
    Not exposed via API - must be called directly from admin scripts.

    Args:
        conn: Database connection
        user_id: User ID to adjust credits for
        amount: Amount to adjust (positive = add, negative = deduct)
        admin_id: Admin user ID performing the adjustment
        reason: Reason for the adjustment (e.g., "Customer service refund", "Promotional bonus")

    Returns:
        Transaction ID of the created credit_transaction
    """
    # Get current balance
    balance_before = await get_balance(conn, user_id)
    balance_after = balance_before + amount

    # Update balance
    if amount > 0:
        await billing_queries.update_user_balance(
            conn, user_id, balance_after, last_credited_at=get_current_timestamp()
        )
        # Update lifetime stats
        await billing_queries.update_lifetime_stats(conn, user_id, earned_delta=amount)
    else:
        await billing_queries.update_user_balance(
            conn, user_id, balance_after, last_spent_at=get_current_timestamp()
        )
        # Update lifetime stats
        await billing_queries.update_lifetime_stats(conn, user_id, spent_delta=-amount)

    # Record transaction
    transaction_id = await billing_queries.create_credit_transaction(
        conn=conn,
        user_id=user_id,
        transaction_type="adjustment",
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_after,
        source_type="admin_adjustment",
        source_id=None,
        description=reason,
        metadata={
            "admin_id": admin_id,
            "reason": reason,
        },
    )

    return transaction_id
