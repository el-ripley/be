"""
Facebook context message operations.
"""

from typing import Any, Dict, List, Optional

import asyncpg

from src.database.postgres.executor import execute_async_query


async def find_fb_context_messages_to_hide(
    conn: asyncpg.Connection,
    branch_id: str,
    item_type: Optional[str],
    item_id: Optional[str],
    page: Optional[int],
    keep_message_ids: List[str],
) -> List[Dict[str, Any]]:
    """Find FB context messages in branch that match criteria and might be obsolete.

    Args:
        conn: Database connection
        branch_id: Branch UUID
        item_type: FB item type (e.g., 'conv_comments')
        item_id: FB item ID
        page: Page number
        keep_message_ids: Message IDs to exclude from results (the new ones)

    Returns:
        A list of dicts with message_id, type and call_id for matched messages.
    """
    if not item_type or not item_id:
        # Need at least item_type and item_id to match
        return []

    # Build WHERE conditions for metadata matching
    conditions = [
        "b.id = $1",
        "m.metadata->>'item_type' = $2",
        "m.metadata->>'item_id' = $3",
    ]
    params: List[Any] = [branch_id, item_type, item_id]
    param_idx = 4

    if page is not None:
        conditions.append(f"m.metadata->>'page' = ${param_idx}")
        params.append(str(page))
        param_idx += 1
    else:
        # If page is None, match messages where page is NULL or missing
        conditions.append("(m.metadata->>'page' IS NULL OR NOT (m.metadata ? 'page'))")

    # Exclude keep_message_ids
    if keep_message_ids:
        placeholders = ", ".join(
            [f"${i}" for i in range(param_idx, param_idx + len(keep_message_ids))]
        )
        conditions.append(f"m.id::text NOT IN ({placeholders})")
        params.extend(keep_message_ids)

    # First, find messages with metadata matching (function_call_output, human_message)
    query = f"""
        SELECT DISTINCT m.id::text as message_id, m.call_id, m.type
        FROM openai_conversation_branch b
        JOIN unnest(b.message_ids) AS msg_id(id) ON TRUE
        JOIN openai_message m ON m.id = msg_id.id
        LEFT JOIN openai_branch_message_mapping mapping
            ON mapping.message_id = m.id AND mapping.branch_id = b.id
        WHERE {' AND '.join(conditions)}
          AND COALESCE(mapping.is_hidden, FALSE) = FALSE
    """

    try:
        rows = await execute_async_query(conn, query, *params)
        return [
            {
                "message_id": row["message_id"],
                "type": row.get("type"),
                "call_id": row.get("call_id"),
            }
            for row in rows
        ]
    except Exception:
        # Return empty list on error
        return []
