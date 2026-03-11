"""
Agent Communication SQL query functions.
Handles CRUD for conversation_agent_blocks and agent_escalations.
"""

from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from ..executor import (
    execute_async_query,
    execute_async_returning,
    execute_async_scalar,
    execute_async_single,
)
from ..utils import generate_uuid, get_current_timestamp_ms

# ============== conversation_agent_blocks ==============


async def get_active_block(
    conn: asyncpg.Connection,
    conversation_type: str,
    conversation_id: str,
    fan_page_id: str,
) -> Optional[Dict[str, Any]]:
    """Get active block for a conversation."""
    if conversation_type == "messages":
        query = """
            SELECT id, conversation_type, facebook_conversation_messages_id,
                   facebook_conversation_comments_id, fan_page_id,
                   blocked_by, reason, is_active, created_at, updated_at
            FROM conversation_agent_blocks
            WHERE is_active = TRUE
              AND fan_page_id = $1
              AND conversation_type = 'messages'
              AND facebook_conversation_messages_id = $2
            ORDER BY created_at DESC
            LIMIT 1
        """
        row = await execute_async_single(conn, query, fan_page_id, conversation_id)
    else:
        query = """
            SELECT id, conversation_type, facebook_conversation_messages_id,
                   facebook_conversation_comments_id, fan_page_id,
                   blocked_by, reason, is_active, created_at, updated_at
            FROM conversation_agent_blocks
            WHERE is_active = TRUE
              AND fan_page_id = $1
              AND conversation_type = 'comments'
              AND facebook_conversation_comments_id = $2::uuid
            ORDER BY created_at DESC
            LIMIT 1
        """
        row = await execute_async_single(conn, query, fan_page_id, conversation_id)
    return _normalize_block_row(row)


def _normalize_block_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Normalize block row: id as str."""
    if not row:
        return None
    out = dict(row)
    if out.get("id") is not None:
        out["id"] = str(out["id"])
    return out


async def is_conversation_blocked(
    conn: asyncpg.Connection,
    conversation_type: str,
    conversation_id: str,
    fan_page_id: str,
) -> bool:
    """Check if conversation is blocked (for orchestrator guard)."""
    if conversation_type == "messages":
        query = """
            SELECT 1 FROM conversation_agent_blocks
            WHERE is_active = TRUE
              AND fan_page_id = $1
              AND conversation_type = 'messages'
              AND facebook_conversation_messages_id = $2
            LIMIT 1
        """
        val = await execute_async_scalar(conn, query, fan_page_id, conversation_id)
    else:
        query = """
            SELECT 1 FROM conversation_agent_blocks
            WHERE is_active = TRUE
              AND fan_page_id = $1
              AND conversation_type = 'comments'
              AND facebook_conversation_comments_id = $2::uuid
            LIMIT 1
        """
        val = await execute_async_scalar(conn, query, fan_page_id, conversation_id)
    return val is not None


async def upsert_block(
    conn: asyncpg.Connection,
    conversation_type: str,
    conversation_id: str,
    fan_page_id: str,
    blocked_by: str,
    reason: Optional[str],
    is_active: bool,
) -> Dict[str, Any]:
    """Create or update block status."""
    now = get_current_timestamp_ms()
    msg_id = conversation_id if conversation_type == "messages" else None
    cmt_id = conversation_id if conversation_type == "comments" else None

    existing = await get_active_block(
        conn, conversation_type, conversation_id, fan_page_id
    )

    if is_active:
        if existing:
            query = """
                UPDATE conversation_agent_blocks
                SET reason = $1, updated_at = $2
                WHERE id = $3::uuid
                RETURNING id, conversation_type, facebook_conversation_messages_id,
                          facebook_conversation_comments_id, fan_page_id,
                          blocked_by, reason, is_active, created_at, updated_at
            """
            row = await execute_async_returning(
                conn, query, reason, now, existing["id"]
            )
        else:
            block_id = generate_uuid()
            query = """
                INSERT INTO conversation_agent_blocks (
                    id, conversation_type, facebook_conversation_messages_id,
                    facebook_conversation_comments_id, fan_page_id,
                    blocked_by, reason, is_active, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, $8, $8)
                RETURNING id, conversation_type, facebook_conversation_messages_id,
                          facebook_conversation_comments_id, fan_page_id,
                          blocked_by, reason, is_active, created_at, updated_at
            """
            row = await execute_async_returning(
                conn,
                query,
                block_id,
                conversation_type,
                msg_id,
                cmt_id,
                fan_page_id,
                blocked_by,
                reason,
                now,
            )
    else:
        if existing:
            query = """
                UPDATE conversation_agent_blocks
                SET is_active = FALSE, updated_at = $1
                WHERE id = $2::uuid
                RETURNING id, conversation_type, facebook_conversation_messages_id,
                          facebook_conversation_comments_id, fan_page_id,
                          blocked_by, reason, is_active, created_at, updated_at
            """
            row = await execute_async_returning(conn, query, now, existing["id"])
        else:
            row = {
                "id": None,
                "conversation_type": conversation_type,
                "facebook_conversation_messages_id": msg_id,
                "facebook_conversation_comments_id": cmt_id,
                "fan_page_id": fan_page_id,
                "blocked_by": blocked_by,
                "reason": reason,
                "is_active": False,
                "created_at": None,
                "updated_at": now,
            }
    return _normalize_block_row(row) or row


# ============== agent_escalations ==============


def _build_escalations_where(
    owner_user_id: str,
    conversation_type: Optional[str] = None,
    fan_page_id: Optional[str] = None,
    facebook_conversation_messages_id: Optional[str] = None,
    facebook_conversation_comments_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    created_at_from: Optional[int] = None,
    created_at_to: Optional[int] = None,
) -> tuple[str, List[Any]]:
    """Build WHERE clause and params for escalation list/count.
    created_at_from/created_at_to: Unix timestamp in milliseconds (inclusive).
    """
    conditions = ["owner_user_id = $1"]
    params: List[Any] = [owner_user_id]
    idx = 2
    if conversation_type:
        conditions.append(f"conversation_type = ${idx}")
        params.append(conversation_type)
        idx += 1
    if fan_page_id:
        conditions.append(f"fan_page_id = ${idx}")
        params.append(fan_page_id)
        idx += 1
    if facebook_conversation_messages_id:
        conditions.append(f"facebook_conversation_messages_id = ${idx}")
        params.append(facebook_conversation_messages_id)
        idx += 1
    if facebook_conversation_comments_id:
        conditions.append(f"facebook_conversation_comments_id = ${idx}::uuid")
        params.append(facebook_conversation_comments_id)
        idx += 1
    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1
    if priority:
        conditions.append(f"priority = ${idx}")
        params.append(priority)
        idx += 1
    if created_at_from is not None:
        conditions.append(f"created_at >= ${idx}")
        params.append(created_at_from)
        idx += 1
    if created_at_to is not None:
        conditions.append(f"created_at <= ${idx}")
        params.append(created_at_to)
        idx += 1
    return " AND ".join(conditions), params


async def get_escalations_with_filters(
    conn: asyncpg.Connection,
    owner_user_id: str,
    conversation_type: Optional[str] = None,
    fan_page_id: Optional[str] = None,
    facebook_conversation_messages_id: Optional[str] = None,
    facebook_conversation_comments_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    created_at_from: Optional[int] = None,
    created_at_to: Optional[int] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Get escalations with comprehensive filters."""
    where_clause, params = _build_escalations_where(
        owner_user_id,
        conversation_type=conversation_type,
        fan_page_id=fan_page_id,
        facebook_conversation_messages_id=facebook_conversation_messages_id,
        facebook_conversation_comments_id=facebook_conversation_comments_id,
        status=status,
        priority=priority,
        created_at_from=created_at_from,
        created_at_to=created_at_to,
    )
    params.extend([limit, offset])
    param_len = len(params)
    query = f"""
        SELECT id, conversation_type, facebook_conversation_messages_id,
               facebook_conversation_comments_id, fan_page_id, owner_user_id,
               created_by, subject, priority, status,
               created_at, updated_at, suggest_response_history_id
        FROM agent_escalations
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ${param_len - 1} OFFSET ${param_len}
    """
    rows = await execute_async_query(conn, query, *params)
    return [_normalize_escalation_row(r) for r in rows]


async def count_escalations_with_filters(
    conn: asyncpg.Connection,
    owner_user_id: str,
    conversation_type: Optional[str] = None,
    fan_page_id: Optional[str] = None,
    facebook_conversation_messages_id: Optional[str] = None,
    facebook_conversation_comments_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    created_at_from: Optional[int] = None,
    created_at_to: Optional[int] = None,
) -> int:
    """Count escalations matching filters."""
    where_clause, params = _build_escalations_where(
        owner_user_id,
        conversation_type=conversation_type,
        fan_page_id=fan_page_id,
        facebook_conversation_messages_id=facebook_conversation_messages_id,
        facebook_conversation_comments_id=facebook_conversation_comments_id,
        status=status,
        priority=priority,
        created_at_from=created_at_from,
        created_at_to=created_at_to,
    )
    query = f"""
        SELECT COUNT(*) AS total FROM agent_escalations WHERE {where_clause}
    """
    row = await execute_async_single(conn, query, *params)
    return row.get("total", 0) if row else 0


def _normalize_escalation_row(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize escalation row: UUIDs as str."""
    if not row:
        return {}
    out = dict(row)
    for field in (
        "id",
        "facebook_conversation_comments_id",
        "suggest_response_history_id",
    ):
        if out.get(field) is not None:
            out[field] = str(out[field])
    return out


async def get_escalation_by_id(
    conn: asyncpg.Connection,
    escalation_id: str,
) -> Optional[Dict[str, Any]]:
    """Get single escalation by ID."""
    query = """
        SELECT id, conversation_type, facebook_conversation_messages_id,
               facebook_conversation_comments_id, fan_page_id, owner_user_id,
               created_by, subject, priority, status,
               created_at, updated_at, suggest_response_history_id
        FROM agent_escalations
        WHERE id = $1::uuid
    """
    row = await execute_async_single(conn, query, escalation_id)
    return _normalize_escalation_row(row) if row else None


async def update_escalation_status(
    conn: asyncpg.Connection,
    escalation_id: str,
    status: str,
) -> Optional[Dict[str, Any]]:
    """Update escalation status (open/closed) and updated_at."""
    now = get_current_timestamp_ms()
    query = """
        UPDATE agent_escalations
        SET status = $1, updated_at = $2
        WHERE id = $3::uuid
        RETURNING id, conversation_type, facebook_conversation_messages_id,
                  facebook_conversation_comments_id, fan_page_id, owner_user_id,
                  created_by, subject, priority, status,
                  created_at, updated_at, suggest_response_history_id
    """
    row = await execute_async_returning(conn, query, status, now, escalation_id)
    return _normalize_escalation_row(row) if row else None


# ============== agent_escalation_messages ==============


def _normalize_escalation_message_row(
    row: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Normalize escalation message row: UUIDs as str."""
    if not row:
        return None
    out = dict(row)
    for field in ("id", "escalation_id"):
        if out.get(field) is not None:
            out[field] = str(out[field])
    return out


async def get_escalation_messages(
    conn: asyncpg.Connection,
    escalation_id: str,
) -> List[Dict[str, Any]]:
    """List messages for an escalation, ordered chronologically."""
    query = """
        SELECT id, escalation_id, sender_type, content, context_snapshot, created_at
        FROM agent_escalation_messages
        WHERE escalation_id = $1::uuid
        ORDER BY created_at ASC
    """
    rows = await execute_async_query(conn, query, escalation_id)
    return [
        _normalize_escalation_message_row(r)  # type: ignore[misc]
        for r in rows
        if _normalize_escalation_message_row(r) is not None
    ]


async def insert_escalation_message(
    conn: asyncpg.Connection,
    escalation_id: str,
    sender_type: str,
    content: str,
    context_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Add a message to an escalation thread."""
    import json as _json

    msg_id = generate_uuid()
    now = get_current_timestamp_ms()
    snapshot_json = _json.dumps(context_snapshot) if context_snapshot else None

    query = """
        INSERT INTO agent_escalation_messages (id, escalation_id, sender_type, content, context_snapshot, created_at)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb, $6)
        RETURNING id, escalation_id, sender_type, content, context_snapshot, created_at
    """
    row = await execute_async_returning(
        conn, query, msg_id, escalation_id, sender_type, content, snapshot_json, now
    )
    return _normalize_escalation_message_row(row) or row  # type: ignore[return-value]


async def get_escalations_for_context(
    conn: asyncpg.Connection,
    conversation_type: str,
    conversation_id: str,
    fan_page_id: str,
    owner_user_id: str,
    limit: int = 10,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Get open escalations with messages for context builder.
    Order: last_activity DESC (only open escalations).
    Returns (escalations, total_count). Max `limit` escalations; all include messages.
    """
    if conversation_type == "messages":
        conv_col = "facebook_conversation_messages_id"
        conv_val = conversation_id
        conv_cast = ""
    else:
        conv_col = "facebook_conversation_comments_id"
        conv_val = conversation_id
        conv_cast = "::uuid"

    query = f"""
        WITH filtered AS (
            SELECT e.id, e.subject, e.priority, e.status, e.updated_at,
                GREATEST(
                    e.updated_at,
                    COALESCE(
                        (SELECT MAX(em.created_at) FROM agent_escalation_messages em WHERE em.escalation_id = e.id),
                        e.updated_at
                    )
                ) AS last_activity
            FROM agent_escalations e
            WHERE e.fan_page_id = $1
              AND e.owner_user_id = $2
              AND e.conversation_type = $3
              AND e.{conv_col} = $4{conv_cast}
              AND e.status = 'open'
        ),
        ordered AS (
            SELECT *, COUNT(*) OVER () AS total_count
            FROM filtered
            ORDER BY last_activity DESC
        )
        SELECT id, subject, priority, status, updated_at, total_count
        FROM ordered
        LIMIT $5
    """
    rows = await execute_async_query(
        conn, query, fan_page_id, owner_user_id, conversation_type, conv_val, limit
    )
    if not rows:
        return [], 0

    total_count = int(rows[0]["total_count"]) if rows else 0
    all_ids = [str(r["id"]) for r in rows]

    escalations: List[Dict[str, Any]] = []
    for r in rows:
        eid = str(r["id"])
        esc = {
            "id": eid,
            "subject": r["subject"],
            "priority": r["priority"],
            "status": r["status"],
            "updated_at": r["updated_at"],
            "messages": [],
        }
        escalations.append(esc)

    if all_ids:
        msg_query = """
            SELECT em.escalation_id, em.id AS msg_id, em.sender_type, em.content, em.created_at AS msg_created_at
            FROM agent_escalation_messages em
            WHERE em.escalation_id = ANY($1::uuid[])
            ORDER BY em.created_at ASC
        """
        msg_rows = await execute_async_query(conn, msg_query, all_ids)
        esc_by_id = {e["id"]: e for e in escalations}
        for mr in msg_rows:
            eid = str(mr["escalation_id"])
            if eid in esc_by_id:
                esc_by_id[eid]["messages"].append(
                    {
                        "id": str(mr["msg_id"]),
                        "sender_type": mr["sender_type"],
                        "content": mr["content"],
                        "created_at": mr["msg_created_at"],
                    }
                )

    return escalations, total_count


async def get_escalation_list_minimal(
    conn: asyncpg.Connection,
    conversation_type: str,
    conversation_id: str,
    fan_page_id: str,
    owner_user_id: str,
    limit: int = 10,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Get minimal escalation info (no messages) for system prompt injection.
    Returns (list of at most `limit` rows, total_count). Sorted by updated_at DESC.
    """
    if conversation_type == "messages":
        conv_col = "facebook_conversation_messages_id"
        conv_cast = ""
    else:
        conv_col = "facebook_conversation_comments_id"
        conv_cast = "::uuid"

    query = f"""
        WITH ordered AS (
            SELECT id, subject, priority, status, updated_at,
                   COUNT(*) OVER () AS total_count
            FROM agent_escalations
            WHERE fan_page_id = $1
              AND owner_user_id = $2
              AND conversation_type = $3
              AND {conv_col} = $4{conv_cast}
            ORDER BY updated_at DESC
        )
        SELECT id, subject, priority, status, updated_at, total_count
        FROM ordered
        LIMIT $5
    """
    rows = await execute_async_query(
        conn,
        query,
        fan_page_id,
        owner_user_id,
        conversation_type,
        conversation_id,
        limit,
    )
    if not rows:
        return [], 0
    total_count = int(rows[0]["total_count"])
    list_result = [
        {
            "id": str(r["id"]),
            "subject": r["subject"],
            "priority": r["priority"],
            "status": r["status"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    return list_result, total_count


async def get_open_escalations_with_messages(
    conn: asyncpg.Connection,
    conversation_type: str,
    conversation_id: str,
    fan_page_id: str,
    owner_user_id: str,
) -> List[Dict[str, Any]]:
    """
    Legacy: Get open escalations with messages.
    Prefer get_escalations_for_context for suggest response context.
    """
    escalations, _ = await get_escalations_for_context(
        conn, conversation_type, conversation_id, fan_page_id, owner_user_id, limit=100
    )
    return [e for e in escalations if e["status"] == "open"]
