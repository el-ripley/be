"""Playbook-related SQL query functions."""

from typing import Any, Dict, List

import asyncpg


async def get_playbooks_by_ids(
    conn: asyncpg.Connection,
    playbook_ids: List[str],
) -> List[Dict[str, Any]]:
    """
    Get playbook rows by IDs (id, title, situation, content). Only active (deleted_at IS NULL).

    Args:
        conn: Database connection
        playbook_ids: List of playbook UUID strings

    Returns:
        List of dicts with id (str), title, situation, content
    """
    if not playbook_ids:
        return []
    rows = await conn.fetch(
        """
        SELECT id, title, situation, content
        FROM page_playbooks
        WHERE id = ANY($1::uuid[]) AND deleted_at IS NULL
        ORDER BY title
        """,
        playbook_ids,
    )
    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "situation": r["situation"] or "",
            "content": r["content"] or "",
        }
        for r in rows
    ]


async def get_assigned_playbook_ids(
    conn: asyncpg.Connection,
    page_admin_id: str,
    conversation_type: str,
) -> List[str]:
    """
    Get playbook IDs assigned to a page_admin for a conversation type.

    Args:
        conn: Database connection
        page_admin_id: facebook_page_admins.id
        conversation_type: 'messages' or 'comments'

    Returns:
        List of playbook_id strings (UUIDs)
    """
    rows = await conn.fetch(
        """
        SELECT playbook_id FROM page_playbook_assignments
        WHERE page_admin_id = $1 AND conversation_type = $2 AND deleted_at IS NULL
        """,
        page_admin_id,
        conversation_type,
    )
    return [str(r["playbook_id"]) for r in rows]
