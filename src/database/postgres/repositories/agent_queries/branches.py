"""
Branch operations.
"""

import json
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import asyncpg

from src.database.postgres.executor import (
    execute_async_command,
    execute_async_query,
    execute_async_single,
)
from src.database.postgres.utils import generate_uuid, get_current_timestamp

from .conversations import get_user_conversation_count_for_title
from .messages import _decode_json_fields


def _sanitize_for_postgres(value: Any) -> Any:
    """Remove null characters (\u0000) that PostgreSQL text columns cannot store.

    PostgreSQL raises "unsupported Unicode escape sequence" for \u0000.
    This recursively sanitizes strings in dicts, lists, and plain strings.
    """
    if value is None:
        return None

    if isinstance(value, str):
        # Remove null characters - PostgreSQL cannot store \u0000 in text
        return value.replace("\x00", "").replace("\\u0000", "")

    if isinstance(value, dict):
        return {k: _sanitize_for_postgres(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_sanitize_for_postgres(item) for item in value]

    return value


def _normalize_json_payload(value: Any) -> Optional[Any]:
    if value is None:
        return None

    if isinstance(value, str):
        # Sanitize null characters first
        value = _sanitize_for_postgres(value)
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
            # Sanitize parsed content as well
            return _sanitize_for_postgres(parsed)
        except json.JSONDecodeError:
            return json.dumps(value, ensure_ascii=False)

    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return _sanitize_for_postgres(dumped)

    if isinstance(value, (dict, list)):
        return _sanitize_for_postgres(value)

    if isinstance(value, (int, float, bool)):
        return value

    return json.dumps(value, ensure_ascii=False)


async def create_conversation_with_master_branch(
    conn: asyncpg.Connection,
    user_id: str,
    title: Optional[str] = None,
) -> tuple[str, str]:
    """Create conversation with master branch (no initial message).

    If title is None, auto-generates title as "conv_{count + 1}".

    Returns:
        tuple[str, str]: (conversation_id, branch_id)
    """
    conversation_id = generate_uuid()
    branch_id = generate_uuid()
    created_at = get_current_timestamp()

    # Auto-generate title if not provided
    if title is None:
        count = await get_user_conversation_count_for_title(conn, user_id)
        title = f"conv_{count + 1}"

    # Get default settings
    from src.agent.common.conversation_settings import get_default_settings

    default_settings = get_default_settings()
    settings_json = json.dumps(default_settings)

    # Step 1: Create conversation
    conversation_data = {
        "id": conversation_id,
        "user_id": user_id,
        "title": title,
        "current_branch_id": None,
        "message_sequence_counter": 0,
        "oldest_message_id": None,
        "settings": settings_json,  # JSON string for JSONB field
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

    # Step 2: Create master branch with EMPTY message_ids
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
        SET current_branch_id = $1,
            updated_at = $2
        WHERE id = $3
        """,
        branch_id,
        created_at,
        conversation_id,
    )

    return conversation_id, branch_id


async def create_branch(
    conn: asyncpg.Connection,
    conversation_id: str,
    created_from_message_id: str,
    created_from_branch_id: str,
    branch_name: Optional[str] = None,
    should_switch: bool = True,
) -> str:
    """Create a new branch and optionally switch to it."""
    branch_id = generate_uuid()
    created_at = get_current_timestamp()

    # Get parent branch message_ids
    parent_query = """
        SELECT message_ids::text[] AS message_ids
        FROM openai_conversation_branch
        WHERE id = $1
    """
    parent_row = await execute_async_single(conn, parent_query, created_from_branch_id)

    if not parent_row:
        raise ValueError("Parent branch not found")

    parent_message_ids = parent_row["message_ids"]

    # Find the index of the message we're branching from
    try:
        branch_index = parent_message_ids.index(created_from_message_id)
        # Include messages up to and including the branch point
        new_message_ids = parent_message_ids[: branch_index + 1]
    except ValueError:
        raise ValueError("Message not found in parent branch")

    new_message_uuid_list = [UUID(message_id) for message_id in new_message_ids]

    # Create new branch
    branch_data = {
        "id": branch_id,
        "conversation_id": conversation_id,
        "created_from_message_id": created_from_message_id,
        "created_from_branch_id": created_from_branch_id,
        "message_ids": new_message_uuid_list,
        "branch_name": branch_name,
        "is_active": False,
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

    # Copy message mappings from parent branch
    if new_message_ids:
        mapping_query = """
        INSERT INTO openai_branch_message_mapping (
            message_id,
            branch_id,
            is_modified,
            modified_content,
            modified_reasoning_summary,
            modified_function_arguments,
            modified_function_output,
            is_hidden,
            created_at
        )
        SELECT 
            message_id,
            $1,
            is_modified,
            modified_content,
            modified_reasoning_summary,
            modified_function_arguments,
            modified_function_output,
            is_hidden,
            $2
        FROM openai_branch_message_mapping 
        WHERE branch_id = $3 AND message_id = ANY($4::uuid[])
        """
        await execute_async_command(
            conn,
            mapping_query,
            branch_id,
            created_at,
            created_from_branch_id,
            new_message_uuid_list,
        )

    # Switch to new branch if requested
    if should_switch:
        await update_conversation(conn, conversation_id, branch_id)

    return branch_id


async def create_branch_before_message(
    conn: asyncpg.Connection,
    conversation_id: str,
    target_message_id: str,
    source_branch_id: str,
    branch_name: Optional[str] = None,
) -> str:
    """Create branch from messages BEFORE target_message_id.

    Example: Branch A-B-C-D, target=C
    New branch will have: A-B (không bao gồm C)

    If target is the first message (index=0), creates empty branch.

    Args:
        conn: Database connection
        conversation_id: Conversation ID
        target_message_id: Message ID to branch before (exclusive)
        source_branch_id: Source branch ID to copy from
        branch_name: Optional branch name

    Returns:
        New branch ID

    Raises:
        ValueError: If source branch not found or target message not in branch
    """
    branch_id = generate_uuid()
    created_at = get_current_timestamp()

    # Get parent branch message_ids
    parent_query = """
        SELECT message_ids::text[] AS message_ids
        FROM openai_conversation_branch
        WHERE id = $1
    """
    parent_row = await execute_async_single(conn, parent_query, source_branch_id)

    if not parent_row:
        raise ValueError("Parent branch not found")

    parent_message_ids = parent_row["message_ids"]

    # Find the index of the target message
    try:
        target_index = parent_message_ids.index(target_message_id)
        # Include messages up to (but not including) the target message
        new_message_ids = parent_message_ids[:target_index]
    except ValueError:
        raise ValueError("Target message not found in source branch")

    new_message_uuid_list = [UUID(message_id) for message_id in new_message_ids]

    # Create new branch
    branch_data = {
        "id": branch_id,
        "conversation_id": conversation_id,
        "created_from_message_id": target_message_id,
        "created_from_branch_id": source_branch_id,
        "message_ids": new_message_uuid_list,
        "branch_name": branch_name,
        "is_active": False,
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

    # Copy message mappings from parent branch
    if new_message_ids:
        mapping_query = """
        INSERT INTO openai_branch_message_mapping (
            message_id,
            branch_id,
            is_modified,
            modified_content,
            modified_reasoning_summary,
            modified_function_arguments,
            modified_function_output,
            is_hidden,
            created_at
        )
        SELECT 
            message_id,
            $1,
            is_modified,
            modified_content,
            modified_reasoning_summary,
            modified_function_arguments,
            modified_function_output,
            is_hidden,
            $2
        FROM openai_branch_message_mapping 
        WHERE branch_id = $3 AND message_id = ANY($4::uuid[])
        """
        await execute_async_command(
            conn,
            mapping_query,
            branch_id,
            created_at,
            source_branch_id,
            new_message_uuid_list,
        )

    # Switch conversation to new branch
    await update_conversation(conn, conversation_id, branch_id)

    return branch_id


async def update_conversation(
    conn: asyncpg.Connection,
    conversation_id: str,
    branch_id: Optional[str] = None,
    title: Optional[str] = None,
) -> bool:
    """Update conversation (switch branch and/or title)."""

    # If branch_id is provided, verify it belongs to the conversation and switch to it
    if branch_id is not None:
        verify_query = "SELECT id FROM openai_conversation_branch WHERE id = $1 AND conversation_id = $2"
        verify_row = await execute_async_single(
            conn, verify_query, branch_id, conversation_id
        )

        if not verify_row:
            return False

        # Deactivate all branches for this conversation
        deactivate_query = """
            UPDATE openai_conversation_branch 
            SET is_active = FALSE, updated_at = $1
            WHERE conversation_id = $2
        """
        await execute_async_command(
            conn, deactivate_query, get_current_timestamp(), conversation_id
        )

        # Activate the target branch
        activate_query = """
            UPDATE openai_conversation_branch 
            SET is_active = TRUE, updated_at = $1
            WHERE id = $2
        """
        await execute_async_command(
            conn, activate_query, get_current_timestamp(), branch_id
        )

    # Update conversation fields based on what's provided
    set_clauses: List[str] = []
    params: List[Any] = []
    param_index = 1

    if branch_id is not None:
        set_clauses.append(f"current_branch_id = ${param_index}")
        params.append(branch_id)
        param_index += 1

    if title is not None:
        set_clauses.append(f"title = ${param_index}")
        params.append(title)
        param_index += 1

    if not set_clauses:
        # Nothing to update
        return False

    set_clauses.append(f"updated_at = ${param_index}")
    params.append(get_current_timestamp())
    param_index += 1

    params.append(conversation_id)

    update_conversation_query = f"""
        UPDATE openai_conversation 
        SET {', '.join(set_clauses)}
        WHERE id = ${param_index}
    """
    await execute_async_command(
        conn,
        update_conversation_query,
        *params,
    )

    return True


async def upsert_message_mapping(
    conn: asyncpg.Connection,
    message_id: str,
    branch_id: str,
    modified_content: Optional[Any] = None,
    modified_reasoning_summary: Optional[Any] = None,
    modified_function_arguments: Optional[Any] = None,
    modified_function_output: Optional[Any] = None,
    is_hidden: bool = False,
) -> str:
    """Upsert message mapping for branch-specific message version."""
    mapping_id = generate_uuid()
    created_at = get_current_timestamp()

    is_modified = any(
        value is not None
        for value in (
            modified_content,
            modified_reasoning_summary,
            modified_function_arguments,
            modified_function_output,
        )
    )

    # Use ON CONFLICT to handle upsert
    query = """
        INSERT INTO openai_branch_message_mapping 
        (
            id,
            message_id,
            branch_id,
            is_modified,
            modified_content,
            modified_reasoning_summary,
            modified_function_arguments,
            modified_function_output,
            is_hidden,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (message_id, branch_id)
        DO UPDATE SET
            is_modified = EXCLUDED.is_modified,
            modified_content = EXCLUDED.modified_content,
            modified_reasoning_summary = EXCLUDED.modified_reasoning_summary,
            modified_function_arguments = EXCLUDED.modified_function_arguments,
            modified_function_output = EXCLUDED.modified_function_output,
            is_hidden = EXCLUDED.is_hidden
        RETURNING id
    """

    normalized_modified_content = _normalize_json_payload(modified_content)
    normalized_reasoning = _normalize_json_payload(modified_reasoning_summary)
    normalized_function_arguments = _normalize_json_payload(modified_function_arguments)
    normalized_function_output = _normalize_json_payload(modified_function_output)

    result = await execute_async_single(
        conn,
        query,
        mapping_id,
        message_id,
        branch_id,
        is_modified,
        normalized_modified_content,
        normalized_reasoning,
        normalized_function_arguments,
        normalized_function_output,
        is_hidden,
        created_at,
    )

    return result["id"] if result else mapping_id


async def get_conversation_branches(
    conn: asyncpg.Connection,
    conversation_id: str,
) -> List[Dict[str, Any]]:
    """Get all branches for a conversation."""
    query = """
        SELECT * FROM openai_conversation_branch 
        WHERE conversation_id = $1 
        ORDER BY created_at ASC
    """
    rows = await execute_async_query(conn, query, conversation_id)
    branches: List[Dict[str, Any]] = []
    for row in rows:
        branch = dict(row)

        # Normalize UUID fields to strings for downstream Pydantic models / JSON responses
        for uuid_field in (
            "id",
            "conversation_id",
            "created_from_message_id",
            "created_from_branch_id",
        ):
            if branch.get(uuid_field) is not None:
                branch[uuid_field] = str(branch[uuid_field])

        if branch.get("message_ids") is not None:
            branch["message_ids"] = [
                str(message_id) for message_id in branch["message_ids"]
            ]

        branches.append(branch)

    return branches


async def get_branch_info(
    conn: asyncpg.Connection,
    branch_id: str,
) -> Optional[Dict[str, Any]]:
    """Get branch information (id and conversation_id) by branch_id.

    Returns:
        Dictionary with 'id' and 'conversation_id' if branch exists, None otherwise
    """
    query = """
        SELECT id, conversation_id 
        FROM openai_conversation_branch 
        WHERE id = $1
    """
    row = await execute_async_single(conn, query, branch_id)

    if row:
        # Normalize UUID fields to strings
        return {
            "id": str(row["id"]),
            "conversation_id": str(row["conversation_id"]),
        }

    return None


async def update_branch_name(
    conn: asyncpg.Connection,
    branch_id: str,
    conversation_id: str,
    branch_name: Optional[str] = None,
) -> bool:
    """Update branch name for a conversation branch."""
    # Verify the branch belongs to the conversation
    verify_query = """
        SELECT id FROM openai_conversation_branch 
        WHERE id = $1 AND conversation_id = $2
    """
    verify_row = await execute_async_single(
        conn, verify_query, branch_id, conversation_id
    )

    if not verify_row:
        return False

    # Update branch name
    update_query = """
        UPDATE openai_conversation_branch 
        SET branch_name = $1, updated_at = $2
        WHERE id = $3
    """
    await execute_async_command(
        conn,
        update_query,
        branch_name,
        get_current_timestamp(),
        branch_id,
    )

    return True


async def get_branch_messages(
    conn: asyncpg.Connection,
    branch_id: str,
    limit: int = 50,
    cursor: Optional[int] = None,
    order: str = "DESC",
) -> Tuple[List[Dict[str, Any]], bool]:
    """Get messages for a branch with mapping information, using cursor-based pagination.

    Args:
        branch_id: Branch ID
        limit: Number of messages to fetch (default: 50)
        cursor: Ordinal position (ord) from previous response for pagination
        order: Sort order - "DESC" for newest first (default), "ASC" for oldest first

    Returns:
        Tuple of (messages list, has_more flag)
    """
    # Validate order parameter
    order_upper = order.upper()
    if order_upper not in ("ASC", "DESC"):
        raise ValueError(f"order must be 'ASC' or 'DESC', got '{order}'")

    query = """
        SELECT 
            msg_id.ord AS ord,
            m.*,
            COALESCE(mapping.is_modified, FALSE) AS is_modified,
            mapping.modified_content,
            mapping.modified_reasoning_summary,
            mapping.modified_function_arguments,
            mapping.modified_function_output,
            COALESCE(mapping.is_hidden, FALSE) AS is_hidden
        FROM openai_conversation_branch b
        JOIN unnest(b.message_ids) WITH ORDINALITY AS msg_id(id, ord) ON TRUE
        JOIN openai_message m ON m.id = msg_id.id
        LEFT JOIN openai_branch_message_mapping mapping 
            ON mapping.message_id = m.id AND mapping.branch_id = b.id
        WHERE b.id = $1
    """

    params: List[Any] = [branch_id]
    param_index = 2

    if cursor is not None:
        # Cursor-based pagination: direction depends on order
        if order_upper == "DESC":
            # For DESC: get messages before this ord position (older messages)
            query += f" AND msg_id.ord < ${param_index}"
        else:
            # For ASC: get messages after this ord position (newer messages)
            query += f" AND msg_id.ord > ${param_index}"
        params.append(cursor)
        param_index += 1

    query += (
        f"""
        ORDER BY msg_id.ord {order_upper}
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

    messages: List[Dict[str, Any]] = []
    for row in rows:
        message = dict(row)
        for uuid_field in ("id", "conversation_id"):
            if message.get(uuid_field) is not None:
                message[uuid_field] = str(message[uuid_field])

        _decode_json_fields(
            message,
            (
                "content",
                "reasoning_summary",
                "function_arguments",
                "function_output",
                "modified_content",
                "modified_reasoning_summary",
                "modified_function_arguments",
                "modified_function_output",
                "metadata",
                "web_search_action",
                "annotations",
            ),
        )
        # Keep ord temporarily for cursor calculation (will be removed in handler)
        messages.append(message)

    return messages, has_more


async def get_all_branch_messages(
    conn: asyncpg.Connection,
    branch_id: str,
    order: str = "DESC",
) -> List[Dict[str, Any]]:
    """Get all messages for a branch (for agent context).

    This function loads all messages by iterating through cursor-based pagination.
    Use this when you need all messages, not just a page.

    Args:
        branch_id: Branch ID
        order: Sort order - "DESC" for newest first (default), "ASC" for oldest first

    Returns:
        List of all messages in the branch
    """
    all_messages: List[Dict[str, Any]] = []
    cursor: Optional[int] = None
    limit = 1000  # Fetch in chunks of 1000

    while True:
        messages, has_more = await get_branch_messages(
            conn=conn,
            branch_id=branch_id,
            limit=limit,
            cursor=cursor,
            order=order,
        )

        all_messages.extend(messages)

        if not has_more:
            break

        # Get cursor from last message for next iteration
        if messages:
            # Find ord of last message (ord is kept in dict from get_branch_messages)
            last_message = messages[-1]
            if "ord" in last_message:
                cursor = last_message["ord"]
            else:
                # If ord not available, we can't continue pagination
                # This should not happen, but break to avoid infinite loop
                break
        else:
            # No messages returned, break
            break

    # Remove ord from all messages before returning (cleanup)
    for msg in all_messages:
        msg.pop("ord", None)

    return all_messages


async def get_branch_message(
    conn: asyncpg.Connection,
    branch_id: str,
    message_id: str,
) -> Optional[Dict[str, Any]]:
    """Get a single branch message with mapping information."""
    query = """
        SELECT 
            m.*,
            COALESCE(mapping.is_modified, FALSE) AS is_modified,
            mapping.modified_content,
            mapping.modified_reasoning_summary,
            mapping.modified_function_arguments,
            mapping.modified_function_output,
            COALESCE(mapping.is_hidden, FALSE) AS is_hidden
        FROM openai_conversation_branch b
        JOIN unnest(b.message_ids) AS msg_id(id) ON TRUE
        JOIN openai_message m ON m.id = msg_id.id
        LEFT JOIN openai_branch_message_mapping mapping 
            ON mapping.message_id = m.id AND mapping.branch_id = b.id
        WHERE b.id = $1 AND m.id = $2
        LIMIT 1
    """

    row = await execute_async_single(conn, query, branch_id, message_id)
    if row is None:
        return None

    message = dict(row)
    for uuid_field in ("id", "conversation_id"):
        if message.get(uuid_field) is not None:
            message[uuid_field] = str(message[uuid_field])
    _decode_json_fields(
        message,
        (
            "content",
            "reasoning_summary",
            "function_arguments",
            "function_output",
            "modified_content",
            "modified_reasoning_summary",
            "modified_function_arguments",
            "modified_function_output",
            "metadata",
        ),
    )
    return message
