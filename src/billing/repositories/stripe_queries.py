"""
Stripe queries - Database operations for Stripe integration.
"""

import json
from typing import Any, Dict, Optional

import asyncpg

from src.database.postgres.executor import execute_async_returning, execute_async_single
from src.database.postgres.utils import generate_uuid, get_current_timestamp


async def get_or_create_stripe_customer(
    conn: asyncpg.Connection,
    user_id: str,
    stripe_customer_id: str,
    email: Optional[str] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Get or create Stripe customer record."""
    # Check if exists by user_id
    query = """
        SELECT * FROM stripe_customers
        WHERE user_id = $1
    """
    existing = await execute_async_single(conn, query, user_id)

    if existing:
        # Update if stripe_customer_id changed
        if existing["stripe_customer_id"] != stripe_customer_id:
            update_query = """
                UPDATE stripe_customers
                SET stripe_customer_id = $1, email = $2, name = $3, updated_at = $4
                WHERE user_id = $5
                RETURNING *
            """
            result = await execute_async_returning(
                conn,
                update_query,
                stripe_customer_id,
                email,
                name,
                get_current_timestamp(),
                user_id,
            )
            return dict(result)
        return dict(existing)

    # Check if stripe_customer_id already exists (different user)
    check_query = """
        SELECT * FROM stripe_customers
        WHERE stripe_customer_id = $1
    """
    existing_stripe = await execute_async_single(conn, check_query, stripe_customer_id)
    if existing_stripe:
        raise ValueError(
            f"Stripe customer {stripe_customer_id} already exists for user {existing_stripe['user_id']}"
        )

    # Create new
    customer_id = generate_uuid()
    created_at = get_current_timestamp()

    insert_query = """
        INSERT INTO stripe_customers
        (id, user_id, stripe_customer_id, email, name, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
    """

    result = await execute_async_returning(
        conn,
        insert_query,
        customer_id,
        user_id,
        stripe_customer_id,
        email,
        name,
        created_at,
        created_at,
    )

    return dict(result)


async def get_stripe_customer_by_user(
    conn: asyncpg.Connection,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Get Stripe customer by user_id."""
    query = """
        SELECT * FROM stripe_customers
        WHERE user_id = $1
    """
    result = await execute_async_single(conn, query, user_id)
    return dict(result) if result else None


async def get_stripe_customer_by_stripe_id(
    conn: asyncpg.Connection,
    stripe_customer_id: str,
) -> Optional[Dict[str, Any]]:
    """Get Stripe customer by stripe_customer_id."""
    query = """
        SELECT * FROM stripe_customers
        WHERE stripe_customer_id = $1
    """
    result = await execute_async_single(conn, query, stripe_customer_id)
    return dict(result) if result else None


async def create_stripe_payment(
    conn: asyncpg.Connection,
    data: Dict[str, Any],
) -> str:
    """Create Stripe payment record."""
    payment_id = generate_uuid()
    created_at = get_current_timestamp()

    metadata_json = json.dumps(data.get("metadata")) if data.get("metadata") else None

    query = """
        INSERT INTO stripe_payments
        (id, user_id, stripe_customer_id, stripe_payment_intent_id,
         stripe_charge_id, stripe_invoice_id, stripe_product_id,
         amount_usd, credits_usd, currency,
         payment_method_type, payment_method_last4, payment_method_brand,
         status, failure_code, failure_message, refunded_amount_usd, refunded_at,
         metadata, paid_at, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22)
        RETURNING id
    """

    result = await execute_async_returning(
        conn,
        query,
        payment_id,
        data["user_id"],
        data["stripe_customer_id"],
        data.get("stripe_payment_intent_id"),
        data.get("stripe_charge_id"),
        data.get("stripe_invoice_id"),
        data.get("stripe_product_id"),
        data["amount_usd"],
        data["credits_usd"],
        data.get("currency", "usd"),
        data.get("payment_method_type"),
        data.get("payment_method_last4"),
        data.get("payment_method_brand"),
        data["status"],
        data.get("failure_code"),
        data.get("failure_message"),
        data.get("refunded_amount_usd", 0),
        data.get("refunded_at"),
        metadata_json,
        data.get("paid_at"),
        created_at,
        created_at,
    )

    return result["id"]


async def upsert_webhook_event(
    conn: asyncpg.Connection,
    event_id: str,
    event_type: str,
    event_data: Dict[str, Any],
    status: str = "pending",
) -> str:
    """Upsert Stripe webhook event (for idempotency)."""
    import json

    # Check if exists
    check_query = """
        SELECT id FROM stripe_webhook_events
        WHERE stripe_event_id = $1
    """
    existing = await conn.fetchval(check_query, event_id)

    if existing:
        # Update status if needed
        # Note: stripe_webhook_events doesn't have updated_at, use processed_at instead
        update_query = """
            UPDATE stripe_webhook_events
            SET status = $1, processed_at = $2
            WHERE stripe_event_id = $3
            RETURNING id
        """
        result = await execute_async_returning(
            conn,
            update_query,
            status,
            get_current_timestamp() if status == "processed" else None,
            event_id,
        )
        return result["id"]

    # Create new
    webhook_id = generate_uuid()
    created_at = get_current_timestamp()
    event_created_at = event_data.get("created", created_at)
    received_at = get_current_timestamp()

    # Extract related IDs for quick lookup
    data_obj = event_data.get("data", {}).get("object", {})
    stripe_customer_id = (
        data_obj.get("customer") if isinstance(data_obj.get("customer"), str) else None
    )
    stripe_payment_intent_id = (
        data_obj.get("payment_intent")
        if isinstance(data_obj.get("payment_intent"), str)
        else None
    )
    stripe_invoice_id = (
        data_obj.get("id")
        if event_type.startswith("invoice.")
        else data_obj.get("invoice")
    )

    query = """
        INSERT INTO stripe_webhook_events
        (id, stripe_event_id, event_type, api_version, status, event_data,
         stripe_customer_id, stripe_payment_intent_id,
         stripe_invoice_id, event_created_at, received_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        RETURNING id
    """

    result = await execute_async_returning(
        conn,
        query,
        webhook_id,
        event_id,
        event_type,
        event_data.get("api_version"),
        status,
        json.dumps(event_data),
        stripe_customer_id,
        stripe_payment_intent_id,
        stripe_invoice_id,
        event_created_at,
        received_at,
    )

    return result["id"]


async def get_stripe_product_by_code(
    conn: asyncpg.Connection,
    product_code: str,
) -> Optional[Dict[str, Any]]:
    """Get Stripe product by code."""
    query = """
        SELECT * FROM stripe_products
        WHERE product_code = $1 AND is_active = TRUE
    """
    result = await execute_async_single(conn, query, product_code)
    return dict(result) if result else None


async def get_stripe_product_by_amount(
    conn: asyncpg.Connection,
    amount_usd: float,
) -> Optional[Dict[str, Any]]:
    """Get Stripe product by amount (returns custom product for any amount)."""
    # Always return custom product since we only have one product now
    query = """
        SELECT * FROM stripe_products
        WHERE product_code = 'topup_custom' AND is_active = TRUE
        LIMIT 1
    """
    result = await execute_async_single(conn, query)
    return dict(result) if result else None


async def check_webhook_event_processed(
    conn: asyncpg.Connection,
    event_id: str,
) -> Optional[str]:
    """Check if webhook event was already processed (returns status if exists)."""
    query = """
        SELECT status FROM stripe_webhook_events
        WHERE stripe_event_id = $1
    """
    result = await conn.fetchval(query, event_id)
    return result
