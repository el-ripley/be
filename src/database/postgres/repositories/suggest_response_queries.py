"""
Suggest Response SQL query functions.
Handles CRUD operations for suggest response agent settings and prompts.
"""

from typing import Optional, Dict, Any, List
import asyncpg
import json
from ..executor import (
    execute_async_single,
    execute_async_returning,
    execute_async_query,
    execute_async_command,
)
from ..utils import generate_uuid, get_current_timestamp_ms


# ================================================================
# SUGGEST RESPONSE AGENT OPERATIONS
# ================================================================


async def get_agent_settings(
    conn: asyncpg.Connection, user_id: str
) -> Optional[Dict[str, Any]]:
    """Get suggest response agent settings for a user."""
    query = """
        SELECT 
            id, user_id, settings, allow_auto_suggest, 
            num_suggest_response, created_at, updated_at
        FROM suggest_response_agent
        WHERE user_id = $1
    """
    return await execute_async_single(conn, query, user_id)


async def upsert_agent_settings(
    conn: asyncpg.Connection,
    user_id: str,
    settings: Dict[str, Any],
    allow_auto_suggest: bool,
    num_suggest_response: int,
) -> Dict[str, Any]:
    """Create or update suggest response agent settings."""
    current_time = get_current_timestamp_ms()
    settings_json = json.dumps(settings)

    query = """
        INSERT INTO suggest_response_agent (
            id, user_id, settings, allow_auto_suggest, 
            num_suggest_response, created_at, updated_at
        ) VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7)
        ON CONFLICT (user_id) DO UPDATE SET
            settings = EXCLUDED.settings,
            allow_auto_suggest = EXCLUDED.allow_auto_suggest,
            num_suggest_response = EXCLUDED.num_suggest_response,
            updated_at = EXCLUDED.updated_at
        RETURNING id, user_id, settings, allow_auto_suggest, 
                  num_suggest_response, created_at, updated_at
    """

    agent_id = generate_uuid()
    return await execute_async_returning(
        conn,
        query,
        agent_id,
        user_id,
        settings_json,
        allow_auto_suggest,
        num_suggest_response,
        current_time,
        current_time,
    )


# ================================================================
# PAGE ADMIN SUGGEST CONFIG OPERATIONS
# ================================================================


def _normalize_page_admin_config_row(
    row: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Normalize DB row for API: id/page_admin_id as str, settings as dict."""
    if not row:
        return None
    out = dict(row)
    if out.get("id") is not None:
        out["id"] = str(out["id"])
    if out.get("page_admin_id") is not None:
        out["page_admin_id"] = str(out["page_admin_id"])
    s = out.get("settings")
    if isinstance(s, str):
        try:
            out["settings"] = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            out["settings"] = {}
    elif not isinstance(s, dict):
        out["settings"] = {}
    if out.get("webhook_delay_seconds") is None:
        out["webhook_delay_seconds"] = 5
    return out


async def get_page_admin_suggest_config(
    conn: asyncpg.Connection, page_admin_id: str
) -> Optional[Dict[str, Any]]:
    """Get suggest response config for a specific page admin. Returns normalized dict (id/page_admin_id str, settings dict)."""
    query = """
        SELECT 
            id, page_admin_id, settings, auto_webhook_suggest, 
            auto_webhook_graph_api, webhook_delay_seconds, created_at, updated_at
        FROM page_admin_suggest_config
        WHERE page_admin_id = $1
    """
    row = await execute_async_single(conn, query, page_admin_id)
    return _normalize_page_admin_config_row(row)


async def get_page_admin_suggest_configs_by_page(
    conn: asyncpg.Connection, page_id: str
) -> List[Dict[str, Any]]:
    """
    Get all suggest response configs for admins of a page.
    Returns page admin records joined with their config (LEFT JOIN - admins without config have null config fields).
    """
    query = """
        SELECT 
            fpa.id AS page_admin_id,
            fpa.facebook_user_id,
            fpa.page_id,
            fpa.access_token,
            fasu.user_id,
            pasc.id AS config_id,
            pasc.settings,
            pasc.auto_webhook_suggest,
            pasc.auto_webhook_graph_api,
            pasc.webhook_delay_seconds
        FROM facebook_page_admins fpa
        INNER JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
        LEFT JOIN page_admin_suggest_config pasc ON fpa.id = pasc.page_admin_id
        WHERE fpa.page_id = $1
    """
    rows = await execute_async_query(conn, query, page_id)
    # Merge config fields into page_admin record for easier consumption
    result = []
    for row in rows:
        record = dict(row)
        if record.get("config_id"):
            record["settings"] = record.get("settings") or {}
            record["auto_webhook_suggest"] = record.get("auto_webhook_suggest", False)
            record["auto_webhook_graph_api"] = record.get(
                "auto_webhook_graph_api", False
            )
            record["webhook_delay_seconds"] = (
                record.get("webhook_delay_seconds")
                if record.get("webhook_delay_seconds") is not None
                else 5
            )
        else:
            # No config - use defaults (gpt-5.2, low verbosity, low reasoning)
            record["settings"] = {
                "model": "gpt-5.2",
                "reasoning": "low",
                "verbosity": "low",
            }
            record["auto_webhook_suggest"] = False
            record["auto_webhook_graph_api"] = False
            record["webhook_delay_seconds"] = 5
        result.append(record)
    return result


async def upsert_page_admin_suggest_config(
    conn: asyncpg.Connection,
    page_admin_id: str,
    settings: Dict[str, Any],
    auto_webhook_suggest: bool,
    auto_webhook_graph_api: bool,
    webhook_delay_seconds: int = 5,
) -> Dict[str, Any]:
    """Create or update page admin suggest config."""
    current_time = get_current_timestamp_ms()
    settings_json = json.dumps(settings)

    query = """
        INSERT INTO page_admin_suggest_config (
            id, page_admin_id, settings, auto_webhook_suggest,
            auto_webhook_graph_api, webhook_delay_seconds, created_at, updated_at
        ) VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8)
        ON CONFLICT (page_admin_id) DO UPDATE SET
            settings = EXCLUDED.settings,
            auto_webhook_suggest = EXCLUDED.auto_webhook_suggest,
            auto_webhook_graph_api = EXCLUDED.auto_webhook_graph_api,
            webhook_delay_seconds = EXCLUDED.webhook_delay_seconds,
            updated_at = EXCLUDED.updated_at
        RETURNING id, page_admin_id, settings, auto_webhook_suggest,
                  auto_webhook_graph_api, webhook_delay_seconds, created_at, updated_at
    """
    config_id = generate_uuid()
    row = await execute_async_returning(
        conn,
        query,
        config_id,
        page_admin_id,
        settings_json,
        auto_webhook_suggest,
        auto_webhook_graph_api,
        webhook_delay_seconds,
        current_time,
        current_time,
    )
    return _normalize_page_admin_config_row(row)


# ================================================================
# SUGGEST RESPONSE MESSAGE OPERATIONS
# ================================================================


async def create_suggest_response_message(
    conn: asyncpg.Connection,
    history_id: str,
    sequence_number: int,
    role: str,
    type: str,
    content: Optional[Dict[str, Any]] = None,
    reasoning_summary: Optional[Dict[str, Any]] = None,
    call_id: Optional[str] = None,
    function_name: Optional[str] = None,
    function_arguments: Optional[Dict[str, Any]] = None,
    function_output: Optional[Dict[str, Any]] = None,
    web_search_action: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
    step: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a message item for suggest response history."""
    message_id = generate_uuid()
    current_time = get_current_timestamp_ms()

    content_json = json.dumps(content) if content else None
    reasoning_json = json.dumps(reasoning_summary) if reasoning_summary else None
    args_json = json.dumps(function_arguments) if function_arguments else None
    output_json = json.dumps(function_output) if function_output else None
    web_search_json = json.dumps(web_search_action) if web_search_action else None
    metadata_json = json.dumps(metadata) if metadata else None
    step_value = step if step is not None else "response_generation"

    query = """
        INSERT INTO suggest_response_message (
            id, history_id, sequence_number, role, type,
            content, reasoning_summary, call_id, function_name,
            function_arguments, function_output, web_search_action, metadata,
            status, step, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10::jsonb, $11::jsonb, $12::jsonb, $13::jsonb, $14, $15, $16)
        RETURNING id, history_id, sequence_number, role, type,
                  content, reasoning_summary, call_id, function_name,
                  function_arguments, function_output, web_search_action, metadata,
                  status, step, created_at
    """
    return await execute_async_returning(
        conn,
        query,
        message_id,
        history_id,
        sequence_number,
        role,
        type,
        content_json,
        reasoning_json,
        call_id,
        function_name,
        args_json,
        output_json,
        web_search_json,
        metadata_json,
        status,
        step_value,
        current_time,
    )


def _normalize_message_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Normalize suggest_response_message DB row for API: id/history_id as str, JSONB fields as dict."""
    if not row:
        return None
    out = dict(row)
    for field in ("id", "history_id"):
        if out.get(field) is not None:
            out[field] = str(out[field])
    for field in (
        "content",
        "metadata",
        "reasoning_summary",
        "function_arguments",
        "function_output",
        "web_search_action",
    ):
        val = out.get(field)
        if isinstance(val, str):
            try:
                out[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                out[field] = None
        elif val is not None and not isinstance(val, dict):
            out[field] = None
    return out


async def get_suggest_response_messages_by_history(
    conn: asyncpg.Connection, history_id: str
) -> List[Dict[str, Any]]:
    """Get all message items for a suggest response history record, ordered by sequence. Returns normalized rows (id/history_id str, JSONB fields dict)."""
    query = """
        SELECT 
            id, history_id, sequence_number, role, type,
            content, reasoning_summary, call_id, function_name,
            function_arguments, function_output, web_search_action, metadata,
            status, step, created_at
        FROM suggest_response_message
        WHERE history_id = $1::uuid
        ORDER BY sequence_number ASC
    """
    rows = await execute_async_query(conn, query, history_id)
    return [r for r in (_normalize_message_row(row) for row in rows) if r is not None]


# ================================================================
# PAGE PROMPTS OPERATIONS
# ================================================================


async def get_active_page_prompt(
    conn: asyncpg.Connection,
    fan_page_id: str,
    prompt_type: str,
    owner_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Get active page prompt for a specific page and prompt type."""
    query = """
        SELECT 
            id, fan_page_id, owner_user_id, prompt_type, 
            created_by_type, is_active, created_at
        FROM page_memory
        WHERE fan_page_id = $1 
          AND prompt_type = $2 
          AND owner_user_id = $3
          AND is_active = TRUE
        ORDER BY created_at DESC
        LIMIT 1
    """
    return await execute_async_single(
        conn, query, fan_page_id, prompt_type, owner_user_id
    )


async def get_active_page_prompt_with_media(
    conn: asyncpg.Connection,
    fan_page_id: str,
    prompt_type: str,
    owner_user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Get active page prompt with its linked media assets.
    JOINs with junction table and media_assets table.
    """
    # Get prompt first
    prompt = await get_active_page_prompt(conn, fan_page_id, prompt_type, owner_user_id)

    if not prompt:
        return None

    # Media is now stored in memory blocks, not in prompt_media junction table
    prompt["media"] = []
    return prompt


async def create_page_prompt(
    conn: asyncpg.Connection,
    fan_page_id: str,
    prompt_type: str,
    content: str,  # Not used (memory stored in blocks), kept for API compatibility
    owner_user_id: str,
    created_by_type: str,
) -> Dict[str, Any]:
    """
    Create a new page prompt and deactivate old ones.
    Append-only pattern: deactivate old, create new.
    Note: content parameter is not used - memory is stored in memory_blocks.
    Memory is now stored in memory_blocks.

    Returns:
        Created prompt record with _old_prompt_ids key containing list of deactivated prompt IDs
    """
    prompt_id = generate_uuid()
    current_time = get_current_timestamp_ms()

    # First, get IDs of existing active prompts before deactivating (for cleanup)
    get_old_prompts_query = """
        SELECT id
        FROM page_memory
        WHERE fan_page_id = $1 
          AND prompt_type = $2 
          AND owner_user_id = $3
          AND is_active = TRUE
    """
    old_prompts = await execute_async_query(
        conn, get_old_prompts_query, fan_page_id, prompt_type, owner_user_id
    )
    old_prompt_ids = [str(p["id"]) for p in old_prompts]

    # Deactivate all existing active prompts
    deactivate_query = """
        UPDATE page_memory
        SET is_active = FALSE
        WHERE fan_page_id = $1 
          AND prompt_type = $2 
          AND owner_user_id = $3
          AND is_active = TRUE
    """
    await execute_async_command(
        conn, deactivate_query, fan_page_id, prompt_type, owner_user_id
    )

    # Cleanup: Delete inactive prompts that are not referenced by suggest_response_history
    await cleanup_unused_page_prompts(conn, fan_page_id, prompt_type, owner_user_id)

    # Then create new active prompt (content column removed from schema)
    insert_query = """
        INSERT INTO page_memory (
            id, fan_page_id, owner_user_id, prompt_type, 
            created_by_type, is_active, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, fan_page_id, owner_user_id, prompt_type, 
                  created_by_type, is_active, created_at
    """

    result = await execute_async_returning(
        conn,
        insert_query,
        prompt_id,
        fan_page_id,
        owner_user_id,
        prompt_type,
        created_by_type,
        True,  # is_active
        current_time,
    )

    # Add old prompt IDs for cleanup
    if result:
        result["_old_prompt_ids"] = old_prompt_ids

    return result


async def cleanup_unused_page_prompts(
    conn: asyncpg.Connection,
    fan_page_id: str,
    prompt_type: str,
    owner_user_id: str,
) -> int:
    """
    Delete inactive page prompts that are not referenced by suggest_response_history.

    Returns:
        Number of prompts deleted
    """
    delete_query = """
        DELETE FROM page_memory
        WHERE fan_page_id = $1 
          AND prompt_type = $2 
          AND owner_user_id = $3
          AND is_active = FALSE
          AND id NOT IN (
              SELECT DISTINCT page_prompt_id
              FROM suggest_response_history
              WHERE page_prompt_id IS NOT NULL
          )
    """
    result = await conn.execute(delete_query, fan_page_id, prompt_type, owner_user_id)
    # result is a string like "DELETE 5", extract the number
    deleted_count = int(result.split()[-1]) if result.split()[-1].isdigit() else 0
    return deleted_count


# ================================================================
# USER MEMORY OPERATIONS (GLOBAL USER-LEVEL MEMORY)
# ================================================================


async def get_active_user_memory(
    conn: asyncpg.Connection,
    owner_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Get active user memory (global user-level memory for general agent)."""
    query = """
        SELECT 
            id, owner_user_id, created_by_type, is_active, created_at
        FROM user_memory
        WHERE owner_user_id = $1 
          AND is_active = TRUE
        ORDER BY created_at DESC
        LIMIT 1
    """
    return await execute_async_single(conn, query, owner_user_id)


async def get_active_user_memory_with_blocks(
    conn: asyncpg.Connection,
    owner_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Get active user memory with its memory_blocks (prompt_type='user_memory')."""
    from .memory_blocks_queries import get_latest_blocks

    um = await get_active_user_memory(conn, owner_user_id)
    if not um:
        return None
    um_id = str(um["id"])
    blocks = await get_latest_blocks(conn, "user_memory", um_id)
    um["blocks"] = blocks
    if um.get("id") is not None:
        um["id"] = str(um["id"])
    return um


async def deactivate_user_memory(
    conn: asyncpg.Connection,
    owner_user_id: str,
) -> bool:
    """Soft delete: set is_active=FALSE for active user memory. Returns True if any row was updated."""
    query = """
        UPDATE user_memory
        SET is_active = FALSE
        WHERE owner_user_id = $1 AND is_active = TRUE
    """
    result = await conn.execute(query, owner_user_id)
    # result is like "UPDATE 1"
    return result.split()[-1].isdigit() and int(result.split()[-1]) > 0


# ================================================================
# PAGE SCOPE USER PROMPTS OPERATIONS
# ================================================================


async def get_active_page_scope_user_prompt(
    conn: asyncpg.Connection,
    fan_page_id: str,
    facebook_page_scope_user_id: str,
    owner_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Get active page-scope user prompt for a specific user on a page."""
    query = """
        SELECT 
            id, fan_page_id, facebook_page_scope_user_id, 
            owner_user_id, created_by_type, is_active, created_at
        FROM page_scope_user_memory
        WHERE fan_page_id = $1 
          AND facebook_page_scope_user_id = $2 
          AND owner_user_id = $3
          AND is_active = TRUE
        ORDER BY created_at DESC
        LIMIT 1
    """
    return await execute_async_single(
        conn, query, fan_page_id, facebook_page_scope_user_id, owner_user_id
    )


async def get_active_page_scope_user_prompt_with_media(
    conn: asyncpg.Connection,
    fan_page_id: str,
    facebook_page_scope_user_id: str,
    owner_user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Get active page-scope user prompt with its linked media assets.
    JOINs with junction table and media_assets table.
    """
    # Get prompt first
    prompt = await get_active_page_scope_user_prompt(
        conn, fan_page_id, facebook_page_scope_user_id, owner_user_id
    )

    if not prompt:
        return None

    # Media is now stored in memory blocks, not in prompt_media junction table
    prompt["media"] = []
    return prompt


async def create_page_scope_user_prompt(
    conn: asyncpg.Connection,
    fan_page_id: str,
    facebook_page_scope_user_id: str,
    content: str,  # Not used (memory stored in blocks), kept for API compatibility
    owner_user_id: str,
    created_by_type: str,
) -> Dict[str, Any]:
    """
    Create a new page-scope user prompt and deactivate old ones.
    Append-only pattern: deactivate old, create new.
    Note: content parameter is not used - memory is stored in memory_blocks.
    Memory is now stored in memory_blocks.

    Returns:
        Created prompt record with _old_prompt_ids key containing list of deactivated prompt IDs
    """
    prompt_id = generate_uuid()
    current_time = get_current_timestamp_ms()

    # First, get IDs of existing active prompts before deactivating (for cleanup)
    get_old_prompts_query = """
        SELECT id
        FROM page_scope_user_memory
        WHERE fan_page_id = $1 
          AND facebook_page_scope_user_id = $2 
          AND owner_user_id = $3
          AND is_active = TRUE
    """
    old_prompts = await execute_async_query(
        conn,
        get_old_prompts_query,
        fan_page_id,
        facebook_page_scope_user_id,
        owner_user_id,
    )
    old_prompt_ids = [str(p["id"]) for p in old_prompts]

    # Deactivate all existing active prompts
    deactivate_query = """
        UPDATE page_scope_user_memory
        SET is_active = FALSE
        WHERE fan_page_id = $1 
          AND facebook_page_scope_user_id = $2 
          AND owner_user_id = $3
          AND is_active = TRUE
    """
    await execute_async_command(
        conn, deactivate_query, fan_page_id, facebook_page_scope_user_id, owner_user_id
    )

    # Cleanup: Delete inactive prompts that are not referenced by suggest_response_history
    await cleanup_unused_page_scope_user_prompts(
        conn, fan_page_id, facebook_page_scope_user_id, owner_user_id
    )

    # Then create new active prompt (content column removed from schema)
    insert_query = """
        INSERT INTO page_scope_user_memory (
            id, fan_page_id, facebook_page_scope_user_id, 
            owner_user_id, created_by_type, is_active, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, fan_page_id, facebook_page_scope_user_id, 
                  owner_user_id, created_by_type, is_active, created_at
    """

    result = await execute_async_returning(
        conn,
        insert_query,
        prompt_id,
        fan_page_id,
        facebook_page_scope_user_id,
        owner_user_id,
        created_by_type,
        True,  # is_active
        current_time,
    )

    # Add old prompt IDs for cleanup
    if result:
        result["_old_prompt_ids"] = old_prompt_ids

    return result


async def cleanup_unused_page_scope_user_prompts(
    conn: asyncpg.Connection,
    fan_page_id: str,
    facebook_page_scope_user_id: str,
    owner_user_id: str,
) -> int:
    """
    Delete inactive page-scope user prompts that are not referenced by suggest_response_history.

    Returns:
        Number of prompts deleted
    """
    delete_query = """
        DELETE FROM page_scope_user_memory
        WHERE fan_page_id = $1 
          AND facebook_page_scope_user_id = $2 
          AND owner_user_id = $3
          AND is_active = FALSE
          AND id NOT IN (
              SELECT DISTINCT page_scope_user_prompt_id
              FROM suggest_response_history
              WHERE page_scope_user_prompt_id IS NOT NULL
          )
    """
    result = await conn.execute(
        delete_query, fan_page_id, facebook_page_scope_user_id, owner_user_id
    )
    # result is a string like "DELETE 5", extract the number
    deleted_count = int(result.split()[-1]) if result.split()[-1].isdigit() else 0
    return deleted_count


# ================================================================
# SUGGEST RESPONSE HISTORY OPERATIONS
# ================================================================


async def create_suggest_response_history(
    conn: asyncpg.Connection,
    user_id: str,
    fan_page_id: str,
    conversation_type: str,
    facebook_conversation_messages_id: Optional[str],
    facebook_conversation_comments_id: Optional[str],
    latest_item_id: str,
    latest_item_facebook_time: int,
    page_prompt_id: Optional[str],
    page_scope_user_prompt_id: Optional[str],
    suggestions: List[Dict[str, Any]],
    agent_response_id: str,
    trigger_type: str,
) -> Dict[str, Any]:
    """Create a suggest_response_history record."""
    history_id = generate_uuid()
    current_time = get_current_timestamp_ms()
    suggestions_json = json.dumps(suggestions)
    suggestion_count = len(suggestions)

    query = """
        INSERT INTO suggest_response_history (
            id, user_id, fan_page_id, conversation_type,
            facebook_conversation_messages_id, facebook_conversation_comments_id,
            latest_item_id, latest_item_facebook_time,
            page_prompt_id, page_scope_user_prompt_id,
            suggestions, suggestion_count, agent_response_id, trigger_type,
            selected_suggestion_index, reaction, created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13, $14, NULL, NULL, $15, $15)
        RETURNING id, user_id, fan_page_id, conversation_type,
                  facebook_conversation_messages_id, facebook_conversation_comments_id,
                  latest_item_id, latest_item_facebook_time,
                  page_prompt_id, page_scope_user_prompt_id,
                  suggestions, suggestion_count, agent_response_id, trigger_type,
                  selected_suggestion_index, reaction, created_at, updated_at
    """

    return await execute_async_returning(
        conn,
        query,
        history_id,
        user_id,
        fan_page_id,
        conversation_type,
        facebook_conversation_messages_id,
        facebook_conversation_comments_id,
        latest_item_id,
        latest_item_facebook_time,
        page_prompt_id,
        page_scope_user_prompt_id,
        suggestions_json,
        suggestion_count,
        agent_response_id,
        trigger_type,
        current_time,
    )


async def get_suggest_response_history_by_id(
    conn: asyncpg.Connection, history_id: str
) -> Optional[Dict[str, Any]]:
    """Get a suggest_response_history record by ID."""
    query = """
        SELECT 
            id, user_id, fan_page_id, conversation_type,
            facebook_conversation_messages_id, facebook_conversation_comments_id,
            latest_item_id, latest_item_facebook_time,
            page_prompt_id, page_scope_user_prompt_id,
            suggestions, suggestion_count, agent_response_id, trigger_type,
            selected_suggestion_index, reaction, created_at, updated_at
        FROM suggest_response_history
        WHERE id = $1
    """
    return await execute_async_single(conn, query, history_id)


async def get_suggest_response_history_by_conversation(
    conn: asyncpg.Connection,
    conversation_type: str,
    conversation_id: str,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Get suggest_response_history records for a specific conversation.

    Args:
        conversation_type: 'messages' or 'comments'
        conversation_id:
            - For messages: facebook_conversation_messages.id
            - For comments: facebook_conversation_comments.id (UUID)
        limit: Maximum number of records to return
        offset: Number of records to skip
    """
    if conversation_type == "messages":
        query = """
            SELECT 
                id, user_id, fan_page_id, conversation_type,
                facebook_conversation_messages_id, facebook_conversation_comments_id,
                latest_item_id, latest_item_facebook_time,
                page_prompt_id, page_scope_user_prompt_id,
                suggestions, suggestion_count, agent_response_id, trigger_type,
                selected_suggestion_index, reaction, created_at, updated_at
            FROM suggest_response_history
            WHERE conversation_type = $1 
              AND facebook_conversation_messages_id = $2
            ORDER BY created_at DESC
            LIMIT $3 OFFSET $4
        """
        return await execute_async_query(
            conn, query, conversation_type, conversation_id, limit, offset
        )
    else:  # comments
        query = """
            SELECT 
                id, user_id, fan_page_id, conversation_type,
                facebook_conversation_messages_id, facebook_conversation_comments_id,
                latest_item_id, latest_item_facebook_time,
                page_prompt_id, page_scope_user_prompt_id,
                suggestions, suggestion_count, agent_response_id, trigger_type,
                selected_suggestion_index, reaction, created_at, updated_at
            FROM suggest_response_history
            WHERE conversation_type = $1 
              AND facebook_conversation_comments_id = $2::uuid
            ORDER BY created_at DESC
            LIMIT $3 OFFSET $4
        """
        return await execute_async_query(
            conn, query, conversation_type, conversation_id, limit, offset
        )


async def get_suggest_response_history_by_page(
    conn: asyncpg.Connection,
    fan_page_id: str,
    user_id: str,
    conversation_type: Optional[str] = None,
    trigger_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Get suggest_response_history records for a specific page.

    Args:
        fan_page_id: Facebook page ID
        user_id: User ID
        conversation_type: Optional filter by 'messages' or 'comments'
        trigger_type: Optional filter by 'user' or 'auto'
        limit: Maximum number of records to return
        offset: Number of records to skip
    """
    # Build query with optional filters
    conditions = ["user_id = $1", "fan_page_id = $2"]
    params = [user_id, fan_page_id]
    param_index = 3

    if conversation_type:
        conditions.append(f"conversation_type = ${param_index}")
        params.append(conversation_type)
        param_index += 1

    if trigger_type:
        conditions.append(f"trigger_type = ${param_index}")
        params.append(trigger_type)
        param_index += 1

    where_clause = " AND ".join(conditions)
    params.extend([limit, offset])

    query = f"""
        SELECT 
            id, user_id, fan_page_id, conversation_type,
            facebook_conversation_messages_id, facebook_conversation_comments_id,
            latest_item_id, latest_item_facebook_time,
            page_prompt_id, page_scope_user_prompt_id,
            suggestions, suggestion_count, agent_response_id, trigger_type,
            selected_suggestion_index, reaction, created_at, updated_at
        FROM suggest_response_history
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ${param_index} OFFSET ${param_index + 1}
    """

    return await execute_async_query(conn, query, *params)


async def count_suggest_response_history_by_conversation(
    conn: asyncpg.Connection,
    conversation_type: str,
    conversation_id: str,
) -> int:
    """Count total suggest_response_history records for a conversation."""
    if conversation_type == "messages":
        query = """
            SELECT COUNT(*) as total
            FROM suggest_response_history
            WHERE conversation_type = $1 
              AND facebook_conversation_messages_id = $2
        """
        result = await execute_async_single(
            conn, query, conversation_type, conversation_id
        )
    else:  # comments
        query = """
            SELECT COUNT(*) as total
            FROM suggest_response_history
            WHERE conversation_type = $1 
              AND facebook_conversation_comments_id = $2::uuid
        """
        result = await execute_async_single(
            conn, query, conversation_type, conversation_id
        )

    return result.get("total", 0) if result else 0


async def count_suggest_response_history_by_page(
    conn: asyncpg.Connection,
    fan_page_id: str,
    user_id: str,
    conversation_type: Optional[str] = None,
    trigger_type: Optional[str] = None,
) -> int:
    """Count total suggest_response_history records for a page."""
    # Build query with optional filters
    conditions = ["user_id = $1", "fan_page_id = $2"]
    params = [user_id, fan_page_id]
    param_index = 3

    if conversation_type:
        conditions.append(f"conversation_type = ${param_index}")
        params.append(conversation_type)
        param_index += 1

    if trigger_type:
        conditions.append(f"trigger_type = ${param_index}")
        params.append(trigger_type)
        param_index += 1

    where_clause = " AND ".join(conditions)

    query = f"""
        SELECT COUNT(*) as total
        FROM suggest_response_history
        WHERE {where_clause}
    """

    result = await execute_async_single(conn, query, *params)
    return result.get("total", 0) if result else 0


async def update_suggest_response_history(
    conn: asyncpg.Connection,
    history_id: str,
    selected_suggestion_index: Optional[int] = None,
    reaction: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Update suggest_response_history record with selected_suggestion_index and/or reaction.

    Args:
        history_id: History record UUID
        selected_suggestion_index: Index of selected suggestion (0-based, can be None to clear)
        reaction: 'like' or 'dislike' (can be None to clear)

    Returns:
        Updated record or None if not found
    """
    current_time = get_current_timestamp_ms()

    # Build update query dynamically based on what's provided
    updates = []
    params = []
    param_index = 1

    if selected_suggestion_index is not None:
        updates.append(f"selected_suggestion_index = ${param_index}")
        params.append(selected_suggestion_index)
        param_index += 1

    if reaction is not None:
        # Validate reaction
        if reaction not in ["like", "dislike"]:
            raise ValueError("reaction must be 'like' or 'dislike'")
        updates.append(f"reaction = ${param_index}")
        params.append(reaction)
        param_index += 1

    if not updates:
        # Nothing to update
        return await get_suggest_response_history_by_id(conn, history_id)

    # Always update updated_at
    updates.append(f"updated_at = ${param_index}")
    params.append(current_time)
    param_index += 1

    # Add history_id for WHERE clause
    params.append(history_id)

    update_clause = ", ".join(updates)

    query = f"""
        UPDATE suggest_response_history
        SET {update_clause}
        WHERE id = ${param_index}
        RETURNING id, user_id, fan_page_id, conversation_type,
                  facebook_conversation_messages_id, facebook_conversation_comments_id,
                  latest_item_id, latest_item_facebook_time,
                  page_prompt_id, page_scope_user_prompt_id,
                  suggestions, suggestion_count, agent_response_id, trigger_type,
                  selected_suggestion_index, reaction, created_at, updated_at
    """

    return await execute_async_single(conn, query, *params)


async def get_suggest_response_history_with_filters(
    conn: asyncpg.Connection,
    user_id: str,
    fan_page_id: Optional[str] = None,
    conversation_type: Optional[str] = None,
    facebook_conversation_messages_id: Optional[str] = None,
    facebook_conversation_comments_id: Optional[str] = None,
    page_prompt_id: Optional[str] = None,
    page_scope_user_prompt_id: Optional[str] = None,
    suggestion_count: Optional[int] = None,
    trigger_type: Optional[str] = None,
    reaction: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Get suggest_response_history records with comprehensive filters.

    Args:
        user_id: User ID (required - always filter by user)
        fan_page_id: Optional filter by page
        conversation_type: Optional filter by 'messages' or 'comments'
        facebook_conversation_messages_id: Optional filter by messages conversation ID
        facebook_conversation_comments_id: Optional filter by comments conversation ID
        page_prompt_id: Optional filter by page prompt ID
        page_scope_user_prompt_id: Optional filter by page scope user prompt ID
        suggestion_count: Optional filter by exact suggestion count
        trigger_type: Optional filter by 'user' or 'auto'
        reaction: Optional filter by 'like' or 'dislike'
        limit: Maximum number of records to return
        offset: Number of records to skip

    Returns:
        List of history records
    """
    # Build WHERE clause dynamically
    conditions = ["user_id = $1"]
    params: List[Any] = [user_id]
    param_index = 2

    if fan_page_id:
        conditions.append(f"fan_page_id = ${param_index}")
        params.append(fan_page_id)
        param_index += 1

    if conversation_type:
        conditions.append(f"conversation_type = ${param_index}")
        params.append(conversation_type)
        param_index += 1

    if facebook_conversation_messages_id:
        conditions.append(f"facebook_conversation_messages_id = ${param_index}")
        params.append(facebook_conversation_messages_id)
        param_index += 1

    if facebook_conversation_comments_id:
        conditions.append(f"facebook_conversation_comments_id = ${param_index}::uuid")
        params.append(facebook_conversation_comments_id)
        param_index += 1

    if page_prompt_id:
        conditions.append(f"page_prompt_id = ${param_index}::uuid")
        params.append(page_prompt_id)
        param_index += 1

    if page_scope_user_prompt_id:
        conditions.append(f"page_scope_user_prompt_id = ${param_index}::uuid")
        params.append(page_scope_user_prompt_id)
        param_index += 1

    if suggestion_count is not None:
        conditions.append(f"suggestion_count = ${param_index}")
        params.append(suggestion_count)
        param_index += 1

    if trigger_type:
        conditions.append(f"trigger_type = ${param_index}")
        params.append(trigger_type)
        param_index += 1

    if reaction:
        conditions.append(f"reaction = ${param_index}")
        params.append(reaction)
        param_index += 1

    where_clause = " AND ".join(conditions)
    params.extend([limit, offset])

    query = f"""
        SELECT 
            id, user_id, fan_page_id, conversation_type,
            facebook_conversation_messages_id, facebook_conversation_comments_id,
            latest_item_id, latest_item_facebook_time,
            page_prompt_id, page_scope_user_prompt_id,
            suggestions, suggestion_count, agent_response_id, trigger_type,
            selected_suggestion_index, reaction, created_at, updated_at
        FROM suggest_response_history
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ${param_index} OFFSET ${param_index + 1}
    """

    return await execute_async_query(conn, query, *params)


async def count_suggest_response_history_with_filters(
    conn: asyncpg.Connection,
    user_id: str,
    fan_page_id: Optional[str] = None,
    conversation_type: Optional[str] = None,
    facebook_conversation_messages_id: Optional[str] = None,
    facebook_conversation_comments_id: Optional[str] = None,
    page_prompt_id: Optional[str] = None,
    page_scope_user_prompt_id: Optional[str] = None,
    suggestion_count: Optional[int] = None,
    trigger_type: Optional[str] = None,
    reaction: Optional[str] = None,
) -> int:
    """
    Count suggest_response_history records with comprehensive filters.

    Args:
        user_id: User ID (required - always filter by user)
        fan_page_id: Optional filter by page
        conversation_type: Optional filter by 'messages' or 'comments'
        facebook_conversation_messages_id: Optional filter by messages conversation ID
        facebook_conversation_comments_id: Optional filter by comments conversation ID
        page_prompt_id: Optional filter by page prompt ID
        page_scope_user_prompt_id: Optional filter by page scope user prompt ID
        suggestion_count: Optional filter by exact suggestion count
        trigger_type: Optional filter by 'user' or 'auto'
        reaction: Optional filter by 'like' or 'dislike'

    Returns:
        Total count of matching records
    """
    # Build WHERE clause dynamically (same logic as get function)
    conditions = ["user_id = $1"]
    params: List[Any] = [user_id]
    param_index = 2

    if fan_page_id:
        conditions.append(f"fan_page_id = ${param_index}")
        params.append(fan_page_id)
        param_index += 1

    if conversation_type:
        conditions.append(f"conversation_type = ${param_index}")
        params.append(conversation_type)
        param_index += 1

    if facebook_conversation_messages_id:
        conditions.append(f"facebook_conversation_messages_id = ${param_index}")
        params.append(facebook_conversation_messages_id)
        param_index += 1

    if facebook_conversation_comments_id:
        conditions.append(f"facebook_conversation_comments_id = ${param_index}::uuid")
        params.append(facebook_conversation_comments_id)
        param_index += 1

    if page_prompt_id:
        conditions.append(f"page_prompt_id = ${param_index}::uuid")
        params.append(page_prompt_id)
        param_index += 1

    if page_scope_user_prompt_id:
        conditions.append(f"page_scope_user_prompt_id = ${param_index}::uuid")
        params.append(page_scope_user_prompt_id)
        param_index += 1

    if suggestion_count is not None:
        conditions.append(f"suggestion_count = ${param_index}")
        params.append(suggestion_count)
        param_index += 1

    if trigger_type:
        conditions.append(f"trigger_type = ${param_index}")
        params.append(trigger_type)
        param_index += 1

    if reaction:
        conditions.append(f"reaction = ${param_index}")
        params.append(reaction)
        param_index += 1

    where_clause = " AND ".join(conditions)

    query = f"""
        SELECT COUNT(*) as total
        FROM suggest_response_history
        WHERE {where_clause}
    """

    result = await execute_async_single(conn, query, *params)
    return result.get("total", 0) if result else 0
