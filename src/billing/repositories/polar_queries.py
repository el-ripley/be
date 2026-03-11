"""
Polar queries - Database operations for Polar integration.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

import asyncpg

from src.database.postgres.executor import execute_async_returning, execute_async_single
from src.database.postgres.utils import generate_uuid, get_current_timestamp


def _json_serial(obj: Any) -> Any:
    """Convert non-JSON-serializable values for json.dumps (e.g. datetime from Pydantic model_dump)."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


async def create_polar_payment(
    conn: asyncpg.Connection,
    data: Dict[str, Any],
) -> str:
    """Create Polar payment record."""
    payment_id = generate_uuid()
    created_at = get_current_timestamp()
    metadata_json = (
        json.dumps(data.get("metadata"), default=_json_serial)
        if data.get("metadata")
        else None
    )

    query = """
        INSERT INTO polar_payments
        (id, user_id, polar_order_id, polar_product_id, polar_customer_id,
         amount_usd, credits_usd, currency, status, billing_reason, paid_at,
         created_at, updated_at, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        RETURNING id
    """
    result = await execute_async_returning(
        conn,
        query,
        payment_id,
        data["user_id"],
        data["polar_order_id"],
        data.get("polar_product_id"),
        data.get("polar_customer_id"),
        data["amount_usd"],
        data["credits_usd"],
        data.get("currency", "usd"),
        data["status"],
        data.get("billing_reason"),
        data.get("paid_at"),
        created_at,
        created_at,
        metadata_json,
    )
    return result["id"]


async def get_polar_payment_by_order_id(
    conn: asyncpg.Connection,
    polar_order_id: str,
) -> Optional[Dict[str, Any]]:
    """Get Polar payment by polar_order_id."""
    query = """
        SELECT * FROM polar_payments
        WHERE polar_order_id = $1
    """
    result = await execute_async_single(conn, query, polar_order_id)
    return dict(result) if result else None


async def update_polar_payment_status(
    conn: asyncpg.Connection,
    polar_order_id: str,
    status: str,
    paid_at: Optional[int] = None,
) -> None:
    """Update Polar payment status (e.g. pending -> paid)."""
    updated_at = get_current_timestamp()
    query = """
        UPDATE polar_payments
        SET status = $1, updated_at = $2
    """
    params: list = [status, updated_at]
    if paid_at is not None:
        query += ", paid_at = $3 WHERE polar_order_id = $4"
        params.extend([paid_at, polar_order_id])
    else:
        query += " WHERE polar_order_id = $3"
        params.append(polar_order_id)
    await conn.execute(query, *params)


async def upsert_polar_webhook_event(
    conn: asyncpg.Connection,
    event_id: str,
    event_type: str,
    event_data: Dict[str, Any],
    status: str = "pending",
) -> str:
    """Upsert Polar webhook event (for idempotency)."""
    check_query = """
        SELECT id FROM polar_webhook_events
        WHERE polar_event_id = $1
    """
    existing = await conn.fetchval(check_query, event_id)

    if existing:
        update_query = """
            UPDATE polar_webhook_events
            SET status = $1, processed_at = $2
            WHERE polar_event_id = $3
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

    webhook_id = generate_uuid()
    created_at = get_current_timestamp()
    insert_query = """
        INSERT INTO polar_webhook_events
        (id, polar_event_id, event_type, status, event_data, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
    """
    result = await execute_async_returning(
        conn,
        insert_query,
        webhook_id,
        event_id,
        event_type,
        status,
        json.dumps(event_data, default=_json_serial),
        created_at,
    )
    return result["id"]


async def check_polar_webhook_event_processed(
    conn: asyncpg.Connection,
    event_id: str,
) -> Optional[str]:
    """Check if webhook event was already processed (returns status if exists)."""
    query = """
        SELECT status FROM polar_webhook_events
        WHERE polar_event_id = $1
    """
    return await conn.fetchval(query, event_id)
