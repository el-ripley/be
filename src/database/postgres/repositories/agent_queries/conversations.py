"""
Conversation operations.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from src.database.postgres.executor import (
    execute_async_single,
    execute_async_query,
    execute_async_command,
)
from src.database.postgres.utils import get_current_timestamp, generate_uuid
from src.database.postgres.entities import OpenAIConversation


def _parse_settings_json(settings: Any) -> Optional[Dict[str, Any]]:
    """Parse settings from JSONB field (can be string or dict)."""
    if settings is None:
        return None
    if isinstance(settings, dict):
        return settings
    if isinstance(settings, str):
        try:
            return json.loads(settings)
        except json.JSONDecodeError:
            return None
    return None


async def get_conversation(
    conn: asyncpg.Connection,
    conversation_id: str,
) -> Optional[OpenAIConversation]:
    """Get conversation by ID."""
    query = "SELECT * FROM openai_conversation WHERE id = $1"
    row = await execute_async_single(conn, query, conversation_id)

    if row:
        # Convert UUID fields to string for Pydantic
        row_dict = dict(row)
        if row_dict.get("id"):
            row_dict["id"] = str(row_dict["id"])
        if row_dict.get("user_id"):
            row_dict["user_id"] = str(row_dict["user_id"])
        if row_dict.get("current_branch_id"):
            row_dict["current_branch_id"] = str(row_dict["current_branch_id"])
        if row_dict.get("oldest_message_id"):
            row_dict["oldest_message_id"] = str(row_dict["oldest_message_id"])

        # Parse settings JSONB field
        row_dict["settings"] = _parse_settings_json(row_dict.get("settings"))

        # Convert UUID fields for subagent fields
        if row_dict.get("parent_conversation_id"):
            row_dict["parent_conversation_id"] = str(row_dict["parent_conversation_id"])
        if row_dict.get("parent_agent_response_id"):
            row_dict["parent_agent_response_id"] = str(
                row_dict["parent_agent_response_id"]
            )

        return OpenAIConversation(**row_dict)
    return None


async def get_user_conversations(
    conn: asyncpg.Connection,
    user_id: str,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Get user conversations ordered by latest message, with cursor-based pagination.

    Returns:
        Tuple of (conversations list, has_more flag)
    """
    # Use subquery to get latest message timestamp from current_branch
    # Then order by that timestamp (or conversation created_at if no messages or no branch)
    # Exclude subagent conversations (only show main agent conversations)
    base_query = """
        SELECT
            oc.*,
            COALESCE(
                (
                    SELECT MAX(om.created_at)
                    FROM openai_conversation_branch b
                    JOIN unnest(b.message_ids) AS msg_id(id) ON TRUE
                    JOIN openai_message om ON om.id = msg_id.id
                    WHERE b.id = oc.current_branch_id
                ),
                oc.created_at
            ) as latest_activity_at
        FROM openai_conversation oc
        WHERE oc.user_id = $1
        AND (oc.is_subagent IS NULL OR oc.is_subagent = FALSE)
    """

    params: List[Any] = [user_id]
    param_index = 2

    if cursor:
        # Get the latest_activity_at for the cursor conversation (from current_branch)
        cursor_activity_query = """
            SELECT COALESCE(
                (
                    SELECT MAX(om.created_at)
                    FROM openai_conversation_branch b
                    JOIN unnest(b.message_ids) AS msg_id(id) ON TRUE
                    JOIN openai_message om ON om.id = msg_id.id
                    WHERE b.id = (SELECT current_branch_id FROM openai_conversation WHERE id = $1)
                ),
                (SELECT created_at FROM openai_conversation WHERE id = $1)
            ) as cursor_activity
        """
        cursor_row = await execute_async_single(conn, cursor_activity_query, cursor)
        if not cursor_row:
            # Invalid cursor, return empty
            return [], False

        cursor_activity = cursor_row.get("cursor_activity")

        # Filter: conversations with activity < cursor_activity, or same activity but id < cursor
        base_query += f"""
            AND (
                COALESCE(
                    (
                        SELECT MAX(om.created_at)
                        FROM openai_conversation_branch b
                        JOIN unnest(b.message_ids) AS msg_id(id) ON TRUE
                        JOIN openai_message om ON om.id = msg_id.id
                        WHERE b.id = oc.current_branch_id
                    ),
                    oc.created_at
                ) < ${param_index}
                OR (
                    COALESCE(
                        (
                            SELECT MAX(om.created_at)
                            FROM openai_conversation_branch b
                            JOIN unnest(b.message_ids) AS msg_id(id) ON TRUE
                            JOIN openai_message om ON om.id = msg_id.id
                            WHERE b.id = oc.current_branch_id
                        ),
                        oc.created_at
                    ) = ${param_index}
                    AND oc.id < ${param_index + 1}
                )
            )
        """
        params.append(cursor_activity)
        params.append(cursor)
        param_index += 2

    query = (
        base_query
        + """
        ORDER BY latest_activity_at DESC, oc.id DESC
        LIMIT $"""
        + str(param_index)
        + """
    """
    )

    # Fetch limit + 1 to check if there are more items
    params.append(limit + 1)

    rows = await execute_async_query(conn, query, *params)

    # Check if there are more items
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    # Remove the latest_activity_at field from results (it was just for ordering)
    conversations = []
    for row in rows:
        conv_dict = dict(row)
        conv_dict.pop("latest_activity_at", None)
        # Parse settings JSONB field
        conv_dict["settings"] = _parse_settings_json(conv_dict.get("settings"))
        conversations.append(conv_dict)

    return conversations, has_more


async def get_user_conversations_count(
    conn: asyncpg.Connection,
    user_id: str,
) -> int:
    """Get total count of user conversations."""
    query = "SELECT COUNT(*) as count FROM openai_conversation WHERE user_id = $1"
    result = await execute_async_single(conn, query, user_id)
    return result["count"] if result else 0


async def get_user_conversation_count_for_title(
    conn: asyncpg.Connection,
    user_id: str,
) -> int:
    """Get count of user conversations for auto-generating title."""
    query = "SELECT COUNT(*) as count FROM openai_conversation WHERE user_id = $1"
    result = await execute_async_single(conn, query, user_id)
    return result["count"] if result else 0


async def get_conversation_with_relations(
    conn: asyncpg.Connection,
    conversation_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Get a conversation with one-level related data (branches and Facebook links)."""
    query = """
        SELECT
            oc.*,
            COALESCE(
                jsonb_agg(DISTINCT jsonb_build_object(
                    'id', ocb.id::text,
                    'conversation_id', ocb.conversation_id::text,
                    'created_from_message_id', ocb.created_from_message_id::text,
                    'created_from_branch_id', ocb.created_from_branch_id::text,
                    'message_ids', COALESCE(
                        (
                            SELECT jsonb_agg(msg_id::text)
                            FROM unnest(ocb.message_ids) AS msg(msg_id)
                        ),
                        '[]'::jsonb
                    ),
                    'branch_name', ocb.branch_name,
                    'is_active', ocb.is_active,
                    'created_at', ocb.created_at,
                    'updated_at', ocb.updated_at
                )) FILTER (WHERE ocb.id IS NOT NULL),
                '[]'::jsonb
            ) AS branches
        FROM openai_conversation oc
        LEFT JOIN openai_conversation_branch ocb
            ON oc.id = ocb.conversation_id
        WHERE oc.id = $1 AND oc.user_id = $2
        GROUP BY oc.id
    """

    row = await execute_async_single(conn, query, conversation_id, user_id)
    if not row:
        return None

    data = dict(row)

    # Normalize UUID fields to strings
    for key in ("id", "current_branch_id", "oldest_message_id"):
        if data.get(key):
            data[key] = str(data[key])

    # Ensure counter is int
    data["message_sequence_counter"] = int(data.get("message_sequence_counter", 0))

    # Parse settings JSONB field
    data["settings"] = _parse_settings_json(data.get("settings"))

    # Normalize branches
    branches = data.get("branches") or []
    normalized_branches: List[Dict[str, Any]] = []
    for branch in branches:
        if not branch:
            continue

        if isinstance(branch, dict):
            branch_dict: Dict[str, Any] = dict(branch)
        else:
            try:
                branch_dict = json.loads(branch)
                if not isinstance(branch_dict, dict):
                    continue
            except (TypeError, json.JSONDecodeError):
                continue

        for key in (
            "id",
            "conversation_id",
            "created_from_message_id",
            "created_from_branch_id",
        ):
            if branch_dict.get(key):
                branch_dict[key] = str(branch_dict[key])
        message_ids = branch_dict.get("message_ids") or []
        branch_dict["message_ids"] = [str(msg_id) for msg_id in message_ids]
        normalized_branches.append(branch_dict)
    data["branches"] = normalized_branches

    return data


async def get_conversation_settings(
    conn: asyncpg.Connection,
    conversation_id: str,
) -> Optional[Dict[str, Any]]:
    """Get conversation settings."""
    query = "SELECT settings FROM openai_conversation WHERE id = $1"
    row = await execute_async_single(conn, query, conversation_id)

    if not row:
        return None

    settings = row.get("settings")
    if settings is None:
        return None

    # If settings is a string, parse it
    if isinstance(settings, str):
        try:
            return json.loads(settings)
        except json.JSONDecodeError:
            return None

    # If it's already a dict, return it
    if isinstance(settings, dict):
        return settings

    return None


async def update_conversation_settings(
    conn: asyncpg.Connection,
    conversation_id: str,
    settings: Dict[str, Any],
) -> bool:
    """Update conversation settings."""
    settings_json = json.dumps(settings)
    current_time = get_current_timestamp()

    update_query = """
        UPDATE openai_conversation 
        SET settings = $1::jsonb, updated_at = $2
        WHERE id = $3
    """

    await execute_async_command(
        conn,
        update_query,
        settings_json,
        current_time,
        conversation_id,
    )

    return True


async def create_subagent_conversation(
    conn: asyncpg.Connection,
    user_id: str,
    parent_conversation_id: str,
    parent_agent_response_id: str,
    task_call_id: str,
    subagent_type: str = "explore",
    settings: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Create subagent conversation with master branch.

    Args:
        conn: Database connection
        user_id: User ID
        parent_conversation_id: Parent conversation ID
        parent_agent_response_id: Parent agent response ID
        task_call_id: task call ID
        subagent_type: Type of subagent (default: "explore")
        settings: Optional settings dict (defaults to get_default_settings if None)

    Returns:
        Tuple of (conversation_id, branch_id)
    """
    from src.agent.common.conversation_settings import get_default_settings

    conversation_id = generate_uuid()
    branch_id = generate_uuid()
    created_at = get_current_timestamp()

    # Use provided settings or default
    if settings is None:
        settings = get_default_settings()
    settings_json = json.dumps(settings)

    # Step 1: Create conversation with subagent fields
    conversation_data = {
        "id": conversation_id,
        "user_id": user_id,
        "title": None,  # Subagents don't need titles
        "current_branch_id": None,  # Will be set after branch creation
        "message_sequence_counter": 0,
        "oldest_message_id": None,
        "settings": settings_json,  # JSON string for JSONB field
        "parent_conversation_id": parent_conversation_id,
        "parent_agent_response_id": parent_agent_response_id,
        "subagent_type": subagent_type,
        "is_subagent": True,
        "task_call_id": task_call_id,
        "created_at": created_at,
        "updated_at": created_at,
    }

    # Build SQL with explicit jsonb cast for settings
    columns = list(conversation_data.keys())
    values_placeholders = []
    param_index = 1

    for col in columns:
        if col == "settings":
            # Cast settings to jsonb explicitly
            values_placeholders.append(f"${param_index}::jsonb")
        else:
            values_placeholders.append(f"${param_index}")
        param_index += 1

    await execute_async_command(
        conn,
        f"""
        INSERT INTO openai_conversation ({', '.join(columns)})
        VALUES ({', '.join(values_placeholders)})
        """,
        *list(conversation_data.values()),
    )

    # Step 2: Create master branch
    branch_data = {
        "id": branch_id,
        "conversation_id": conversation_id,
        "message_ids": [],
        "branch_name": "master",
        "is_active": True,
        "created_at": created_at,
        "updated_at": created_at,
    }

    await execute_async_command(
        conn,
        f"""
        INSERT INTO openai_conversation_branch ({', '.join(branch_data.keys())})
        VALUES ({', '.join([f'${i+1}' for i in range(len(branch_data))])})
        """,
        *list(branch_data.values()),
    )

    # Step 3: Update conversation with current_branch_id
    await execute_async_command(
        conn,
        """
        UPDATE openai_conversation 
        SET current_branch_id = $1, updated_at = $2
        WHERE id = $3
        """,
        branch_id,
        created_at,
        conversation_id,
    )

    return conversation_id, branch_id
