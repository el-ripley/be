"""
Agent response operations.
"""

import json
from typing import Any, Dict, List, Optional

import asyncpg

from src.agent.common.agent_types import AGENT_TYPE_GENERAL_AGENT
from src.database.postgres.executor import execute_async_returning, execute_async_single
from src.database.postgres.utils import generate_uuid, get_current_timestamp

from .branches import _normalize_json_payload, _sanitize_for_postgres
from .pricing import calculate_cost


async def create_agent_response(
    conn: asyncpg.Connection,
    user_id: str,
    conversation_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    agent_type: str = AGENT_TYPE_GENERAL_AGENT,
    parent_agent_response_id: Optional[str] = None,
) -> str:
    """Create agent_response record in database."""
    agent_response_id = generate_uuid()
    created_at = get_current_timestamp()

    query = """
        INSERT INTO agent_response 
        (id, user_id, conversation_id, branch_id, agent_type, message_ids,
         model, total_input_tokens, total_output_tokens, total_tokens, total_latency_ms, total_cost, 
         call_count, status, error, parent_agent_response_id, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
        RETURNING id
    """

    result = await execute_async_returning(
        conn,
        query,
        agent_response_id,
        user_id,
        conversation_id,
        branch_id,
        agent_type,
        [],  # Empty message_ids initially
        None,  # model - will be set when first openai_response is created
        0,  # total_input_tokens
        0,  # total_output_tokens
        0,  # total_tokens
        None,  # total_latency_ms
        0,  # total_cost
        0,  # call_count
        "in_progress",  # status
        None,  # error
        parent_agent_response_id,
        created_at,
        created_at,
    )

    return result["id"] if result else agent_response_id


async def update_agent_response_message_ids(
    conn: asyncpg.Connection,
    agent_response_id: str,
    message_ids: List[str],
) -> None:
    """Update agent_response with collected message IDs.

    Appends new message_ids to existing ones, avoiding duplicates.
    This ensures that when resuming from ask_user_question or other scenarios,
    existing message_ids are preserved and not overwritten.
    """
    query = """
        UPDATE agent_response 
        SET message_ids = (
            SELECT ARRAY(
                SELECT DISTINCT unnest(message_ids || $1)
                ORDER BY unnest(message_ids || $1)
            )
        ), updated_at = $2
        WHERE id = $3
    """

    await conn.execute(query, message_ids, get_current_timestamp(), agent_response_id)


async def update_agent_response_aggregates(
    conn: asyncpg.Connection,
    agent_response_id: str,
) -> None:
    """Update agent_response aggregates from linked openai_response records."""
    query = """
        UPDATE agent_response ar
        SET 
            model = COALESCE((
                SELECT model 
                FROM openai_response o 
                WHERE o.agent_response_id = ar.id 
                ORDER BY o.created_at ASC 
                LIMIT 1
            ), ar.model),
            total_input_tokens = COALESCE((
                SELECT SUM(input_tokens) 
                FROM openai_response o 
                WHERE o.agent_response_id = ar.id
            ), 0),
            total_output_tokens = COALESCE((
                SELECT SUM(output_tokens) 
                FROM openai_response o 
                WHERE o.agent_response_id = ar.id
            ), 0),
            total_tokens = COALESCE((
                SELECT SUM(total_tokens) 
                FROM openai_response o 
                WHERE o.agent_response_id = ar.id
            ), 0),
            total_latency_ms = (
                SELECT SUM(latency_ms) 
                FROM openai_response o 
                WHERE o.agent_response_id = ar.id AND o.latency_ms IS NOT NULL
            ),
            total_cost = COALESCE((
                SELECT SUM(total_cost) 
                FROM openai_response o 
                WHERE o.agent_response_id = ar.id
            ), 0),
            call_count = COALESCE((
                SELECT COUNT(*) 
                FROM openai_response o 
                WHERE o.agent_response_id = ar.id
            ), 0),
            -- Don't update status here - only update aggregates
            -- Status should only be updated during finalization (finalize_agent_response or stop_agent_response)
            -- This prevents premature status changes while agent is still running
            error = COALESCE((
                SELECT error 
                FROM openai_response o 
                WHERE o.agent_response_id = ar.id AND o.error IS NOT NULL 
                ORDER BY o.created_at ASC 
                LIMIT 1
            ), ar.error),
            updated_at = $1
        WHERE ar.id = $2
    """

    await conn.execute(query, get_current_timestamp(), agent_response_id)


async def finalize_agent_response(
    conn: asyncpg.Connection,
    agent_response_id: str,
) -> None:
    """Finalize agent_response by updating aggregates and setting final status.

    Note: This function preserves status='stopped' and 'waiting_for_user' if already set.
    Only updates status if it's still 'in_progress'.
    """
    # First update aggregates
    await update_agent_response_aggregates(conn, agent_response_id)

    # Then set final status based on current state
    # IMPORTANT: Preserve status='stopped' and 'waiting_for_user' if already set
    query = """
        UPDATE agent_response ar
        SET 
            status = CASE
                -- Preserve 'stopped' status if already set
                WHEN ar.status = 'stopped' THEN 'stopped'
                -- Preserve 'waiting_for_user' status if already set
                WHEN ar.status = 'waiting_for_user' THEN 'waiting_for_user'
                -- Otherwise determine status from openai_response records
                WHEN EXISTS (
                    SELECT 1 FROM openai_response o 
                    WHERE o.agent_response_id = ar.id AND o.status = 'failed'
                ) THEN 'failed'
                WHEN EXISTS (
                    SELECT 1 FROM openai_response o 
                    WHERE o.agent_response_id = ar.id AND o.status = 'completed'
                ) AND NOT EXISTS (
                    SELECT 1 FROM openai_response o 
                    WHERE o.agent_response_id = ar.id AND o.status != 'completed'
                ) THEN 'completed'
                WHEN EXISTS (
                    SELECT 1 FROM openai_response o 
                    WHERE o.agent_response_id = ar.id AND o.status = 'completed'
                ) AND EXISTS (
                    SELECT 1 FROM openai_response o 
                    WHERE o.agent_response_id = ar.id AND o.status = 'failed'
                ) THEN 'partial'
                ELSE 'completed'
            END,
            updated_at = $1
        WHERE ar.id = $2
    """

    await conn.execute(query, get_current_timestamp(), agent_response_id)


async def stop_agent_response(
    conn: asyncpg.Connection,
    agent_response_id: str,
) -> None:
    """Stop agent_response by updating aggregates and setting status to 'stopped'."""
    # First update aggregates from all saved openai_response records
    # This ensures all billing is captured before stopping
    await update_agent_response_aggregates(conn, agent_response_id)

    # Then set status to 'stopped'
    query = """
        UPDATE agent_response ar
        SET 
            status = 'stopped',
            updated_at = $1
        WHERE ar.id = $2
    """

    await conn.execute(query, get_current_timestamp(), agent_response_id)


async def set_agent_response_waiting(
    conn: asyncpg.Connection,
    agent_response_id: str,
) -> None:
    """Set agent_response status to 'waiting_for_user' when ask_user_question is called."""
    # First update aggregates from all saved openai_response records
    # This ensures all billing is captured before waiting
    await update_agent_response_aggregates(conn, agent_response_id)

    # Then set status to 'waiting_for_user'
    query = """
        UPDATE agent_response ar
        SET 
            status = 'waiting_for_user',
            updated_at = $1
        WHERE ar.id = $2
    """

    await conn.execute(query, get_current_timestamp(), agent_response_id)


async def set_agent_response_in_progress(
    conn: asyncpg.Connection,
    agent_response_id: str,
) -> None:
    """Set agent_response status back to 'in_progress' after resuming from waiting.

    Unlike set_agent_response_waiting/stop_agent_response, this does NOT touch aggregates,
    it only updates the status and timestamp.
    """
    query = """
        UPDATE agent_response ar
        SET 
            status = 'in_progress',
            updated_at = $1
        WHERE ar.id = $2
    """

    await conn.execute(query, get_current_timestamp(), agent_response_id)


async def get_agent_response_id_from_message_id(
    conn: asyncpg.Connection,
    message_id: str,
    conversation_id: str,
    user_id: str,
) -> Optional[str]:
    """Get agent_response_id from message_id.

    Finds the agent_response that contains this message_id.
    Note: Status may be 'waiting_for_user', 'completed', or other - we accept any status
    as long as the message_id matches, because status might have been changed incorrectly.
    """
    query = """
        SELECT ar.id
        FROM agent_response ar
        WHERE ar.conversation_id = $1::uuid
          AND ar.user_id = $2
          AND ar.status = 'waiting_for_user'
          AND $3::uuid = ANY(ar.message_ids)
        ORDER BY ar.created_at DESC
        LIMIT 1
    """

    row = await execute_async_single(conn, query, conversation_id, user_id, message_id)
    return str(row["id"]) if row and row.get("id") else None


async def update_tool_results_function_output(
    conn: asyncpg.Connection,
    message_ids: List[str],
    function_output: str,
) -> None:
    """
    Update function_output for given tool_result (function_call_output) messages.
    """
    if not message_ids:
        return

    query = """
        UPDATE openai_message
        SET function_output = $1, updated_at = $3
        WHERE id = ANY($2::uuid[])
    """

    await conn.execute(query, function_output, message_ids, get_current_timestamp())


async def insert_openai_response_with_agent(
    conn: asyncpg.Connection,
    user_id: str,
    conversation_id: str,
    branch_id: Optional[str],
    agent_response_id: Optional[str],
    response_data: Dict[str, Any],
    input_messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    model: str,
    metadata: Optional[Dict[str, Any]] = None,
    status: str = "completed",
    error_details: Optional[Dict[str, Any]] = None,
) -> str:
    """Save OpenAI response (and optional agent linkage) with branch tracking."""
    openai_response_id = generate_uuid()

    # Extract data from response
    response_id = response_data.get("id", "")
    created_at = response_data.get("created", get_current_timestamp())
    latency_ms = response_data.get("latency_ms")

    # Token usage (may be None for failed responses)
    # Responses API uses input_tokens; legacy Chat Completions uses prompt_tokens
    usage = response_data.get("usage") or {}
    input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    total_tokens = usage.get("total_tokens", 0) or 0
    cached_tokens = usage.get("cached_tokens", 0) or 0
    reasoning_tokens = usage.get("reasoning_tokens", 0) or 0

    # Calculate costs using proper pricing
    costs = calculate_cost(model, input_tokens, output_tokens)
    input_cost = float(costs["input_cost"])
    output_cost = float(costs["output_cost"])
    total_cost = float(costs["total_cost"])

    # Output data
    output_data = response_data.get("output", [])

    # Sanitize all JSON payloads to remove \u0000 null bytes that PostgreSQL cannot store
    input_json = json.dumps(_sanitize_for_postgres(input_messages))
    output_json = json.dumps(_sanitize_for_postgres(output_data))
    tools_json = json.dumps(_sanitize_for_postgres(tools))
    metadata_json = json.dumps(_sanitize_for_postgres(metadata)) if metadata else None
    error_json = (
        json.dumps(_sanitize_for_postgres(error_details)) if error_details else None
    )

    query = """
        INSERT INTO openai_response 
        (id, response_id, user_id, conversation_id, branch_id, agent_response_id,
         model, created_at, latency_ms, input_tokens, output_tokens, total_tokens,
         cached_tokens, reasoning_tokens, input_cost, output_cost, total_cost,
         input, output, tools, metadata, status, error, logged_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24)
        RETURNING id
    """

    result = await execute_async_returning(
        conn,
        query,
        openai_response_id,
        response_id,
        user_id,
        conversation_id,
        branch_id,
        agent_response_id,
        model,
        created_at,
        latency_ms,
        input_tokens,
        output_tokens,
        total_tokens,
        cached_tokens,
        reasoning_tokens,
        input_cost,
        output_cost,
        total_cost,
        input_json,
        output_json,
        tools_json,
        metadata_json,
        status,
        error_json,
        get_current_timestamp(),
    )

    # Update agent_response aggregates if agent_response_id is provided
    if agent_response_id:
        await update_agent_response_aggregates(conn, agent_response_id)

    return result["id"] if result else openai_response_id


async def save_message_and_update_branch(
    conn: asyncpg.Connection,
    conversation_id: str,
    branch_id: str,
    role: str,
    content: Any,
    message_type: str = "message",
    reasoning_summary: Optional[Any] = None,
    function_name: Optional[str] = None,
    function_arguments: Optional[Any] = None,
    function_output: Optional[Any] = None,
    call_id: Optional[str] = None,
    status: Optional[str] = None,
    metadata: Optional[Any] = None,
    message_id: Optional[str] = None,
    web_search_action: Optional[Any] = None,
) -> str:
    """Save message to database and update branch message_ids.

    Args:
        conn: Database connection
        conversation_id: Conversation UUID
        branch_id: Branch UUID
        role: Message role (user, assistant, system, developer, tool)
        content: Message content (string or will be converted to JSONB)
        message_type: Message type (message, reasoning, function_call, etc.)
        reasoning_summary: Reasoning summary payload (for reasoning messages)
        function_name: Name of the function for function_call messages
        function_arguments: Arguments for function_call messages
        function_output: Output for function_call_output messages
        call_id: Identifier linking function call and output
        status: Optional status for the message

    Returns:
        Message ID
    """
    message_id = message_id or generate_uuid()
    created_at = get_current_timestamp()

    # Get current sequence counter and increment it (with row lock)
    sequence_query = """
        UPDATE openai_conversation 
        SET message_sequence_counter = message_sequence_counter + 1
        WHERE id = $1
        RETURNING message_sequence_counter - 1 as sequence_number
    """
    seq_result = await conn.fetchrow(sequence_query, conversation_id)
    sequence_number = seq_result["sequence_number"] if seq_result else 0

    # Normalize content to JSON string for consistent storage
    # Sanitize null characters that PostgreSQL cannot store
    content = _sanitize_for_postgres(content)

    if isinstance(content, (dict, list)):
        json_content = json.dumps(content)
    elif content is None:
        json_content = json.dumps("")
    else:
        if isinstance(content, str):
            stripped = content.strip()
            if not stripped:
                json_content = json.dumps("")
            else:
                try:
                    json.loads(content)
                    json_content = content
                except json.JSONDecodeError:
                    json_content = json.dumps(content)
        else:
            json_content = json.dumps(content)

    json_reasoning_summary = _normalize_json_payload(reasoning_summary)
    json_function_arguments = _normalize_json_payload(function_arguments)
    json_function_output = _normalize_json_payload(function_output)
    json_metadata = _normalize_json_payload(metadata)
    json_web_search_action = _normalize_json_payload(web_search_action)

    # asyncpg expects JSON/JSONB parameters as strings
    if json_reasoning_summary is not None:
        json_reasoning_summary = json.dumps(json_reasoning_summary)
    if json_function_arguments is not None:
        json_function_arguments = json.dumps(json_function_arguments)
    if json_function_output is not None:
        json_function_output = json.dumps(json_function_output)
    if json_metadata is not None:
        json_metadata = json.dumps(json_metadata)
    if json_web_search_action is not None:
        json_web_search_action = json.dumps(json_web_search_action)

    # Insert message with sequence_number and type
    message_query = """
        INSERT INTO openai_message 
        (id, conversation_id, sequence_number, role, type, content,
         reasoning_summary, call_id, function_name,
         function_arguments, function_output, web_search_action,
         status, metadata, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
        RETURNING id
    """

    message_result = await execute_async_returning(
        conn,
        message_query,
        message_id,
        conversation_id,
        sequence_number,
        role,
        message_type,
        json_content,
        json_reasoning_summary,
        call_id,
        function_name,
        json_function_arguments,
        json_function_output,
        json_web_search_action,
        status,
        json_metadata,
        created_at,
        created_at,
    )

    # Update branch message_ids array
    branch_query = """
        UPDATE openai_conversation_branch 
        SET message_ids = array_append(message_ids, $1),
            updated_at = $2
        WHERE id = $3
    """

    await conn.execute(branch_query, message_id, created_at, branch_id)

    return message_result["id"] if message_result else message_id


async def get_latest_conversation_token_count(
    conn: asyncpg.Connection,
    conversation_id: str,
) -> Optional[int]:
    """
    Get the actual token count from the latest openai_response for a conversation.

    This function:
    1. Finds the latest agent_response for the conversation
    2. Gets the last openai_response for that agent_response
    3. Returns the total_tokens from that openai_response

    This provides the actual token count from OpenAI API instead of estimation.

    Args:
        conn: Database connection
        conversation_id: Conversation UUID

    Returns:
        Optional[int]: Total tokens from latest openai_response, or None if not found
    """
    query = """
        SELECT o.total_tokens
        FROM openai_response o
        WHERE o.agent_response_id = (
            SELECT id
            FROM agent_response
            WHERE conversation_id = $1
            ORDER BY created_at DESC
            LIMIT 1
        )
        ORDER BY o.created_at DESC
        LIMIT 1
    """

    result = await conn.fetchval(query, conversation_id)
    return result if result is not None else None


async def get_latest_openai_response_for_conversation(
    conn: asyncpg.Connection,
    conversation_id: str,
    branch_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Get the most recent openai_response for a conversation from the MAIN agent only.
    Excludes responses from sub-agents (summarization, media_description).
    Used to check input_tokens before starting a new agent run.

    Only considers completed responses (status='completed') to avoid failed/incomplete
    responses that may have usage=null and input_tokens=0.

    Args:
        conn: Database connection
        conversation_id: Conversation UUID
        branch_id: Optional branch UUID - when provided, only returns response from this branch

    Returns:
        Optional[Dict[str, Any]]: Latest openai_response data with input_tokens, or None if not found
    """
    # Build query - filter by branch when provided, only completed responses
    branch_filter = "AND oai.branch_id = $2" if branch_id else ""
    params: List[Any] = [conversation_id]
    if branch_id:
        params.append(branch_id)

    query = f"""
        SELECT 
            oai.id, oai.response_id, oai.input_tokens, oai.output_tokens, oai.total_tokens,
            oai.created_at, oai.status
        FROM openai_response oai
        LEFT JOIN agent_response ar ON oai.agent_response_id = ar.id
        WHERE oai.conversation_id = $1
        AND oai.status = 'completed'
        AND (
            ar.parent_agent_response_id IS NULL  -- Main agent (no parent)
            OR oai.agent_response_id IS NULL      -- Old responses without agent_response_id
        )
        {branch_filter}
        ORDER BY oai.created_at DESC
        LIMIT 1
    """

    row = await execute_async_single(conn, query, *params)

    if not row:
        return None

    return dict(row)


async def get_agent_response_for_user(
    conn: asyncpg.Connection,
    agent_response_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Get agent_response if user owns it, otherwise return None.

    Args:
        conn: Database connection
        agent_response_id: Agent response ID
        user_id: User ID to verify ownership

    Returns:
        Optional[Dict[str, Any]]: Agent response data with user_id and status, or None if not found or not owned
    """
    query = """
        SELECT user_id, status, conversation_id, branch_id
        FROM agent_response
        WHERE id = $1 AND user_id = $2
    """

    row = await execute_async_single(conn, query, agent_response_id, user_id)
    return dict(row) if row else None


async def get_sub_agent_responses(
    conn: asyncpg.Connection,
    parent_agent_response_id: str,
) -> List[Dict[str, Any]]:
    """
    Get all child agent_response records for a parent.

    Args:
        conn: Database connection
        parent_agent_response_id: Parent agent_response ID

    Returns:
        List of child agent_response records
    """
    query = """
        SELECT 
            id, user_id, conversation_id, branch_id, agent_type, 
            parent_agent_response_id, model, total_input_tokens, 
            total_output_tokens, total_tokens, total_latency_ms, 
            total_cost, call_count, status, created_at, updated_at
        FROM agent_response
        WHERE parent_agent_response_id = $1
        ORDER BY created_at ASC
    """

    rows = await conn.fetch(query, parent_agent_response_id)
    return [dict(row) for row in rows]


async def get_agent_response_with_hierarchy(
    conn: asyncpg.Connection,
    agent_response_id: str,
) -> Dict[str, Any]:
    """
    Get agent_response with all sub-agent costs aggregated.

    Uses recursive CTE to traverse the hierarchy and calculate total costs.

    Args:
        conn: Database connection
        agent_response_id: Root agent_response ID

    Returns:
        Dict with main agent_response data and aggregated sub-agent costs
    """
    query = """
        WITH RECURSIVE agent_hierarchy AS (
            -- Base case: start with the root agent_response
            SELECT 
                id, parent_agent_response_id, agent_type, model, 
                total_cost, total_tokens, call_count, 0 as depth
            FROM agent_response 
            WHERE id = $1
            
            UNION ALL
            
            -- Recursive case: get all children
            SELECT 
                ar.id, ar.parent_agent_response_id, ar.agent_type, ar.model,
                ar.total_cost, ar.total_tokens, ar.call_count, ah.depth + 1
            FROM agent_response ar
            JOIN agent_hierarchy ah ON ar.parent_agent_response_id = ah.id
        )
        SELECT 
            -- Main agent data
            (SELECT * FROM agent_response WHERE id = $1) as main_agent,
            -- Aggregated sub-agent costs
            COALESCE(SUM(total_cost), 0) as sub_agents_total_cost,
            COALESCE(SUM(total_tokens), 0) as sub_agents_total_tokens,
            COALESCE(SUM(call_count), 0) as sub_agents_call_count,
            -- Hierarchy details
            json_agg(
                json_build_object(
                    'id', id,
                    'agent_type', agent_type,
                    'model', model,
                    'total_cost', total_cost,
                    'total_tokens', total_tokens,
                    'call_count', call_count,
                    'depth', depth
                ) ORDER BY depth, created_at
            ) FILTER (WHERE depth > 0) as sub_agents
        FROM agent_hierarchy
        WHERE depth > 0
    """

    row = await execute_async_single(conn, query, agent_response_id)

    if not row:
        # If no hierarchy, just return the main agent
        main_query = """
            SELECT * FROM agent_response WHERE id = $1
        """
        main_row = await execute_async_single(conn, main_query, agent_response_id)
        if main_row:
            result = dict(main_row)
            result["sub_agents_total_cost"] = 0
            result["sub_agents_total_tokens"] = 0
            result["sub_agents_call_count"] = 0
            result["sub_agents"] = []
            return result
        return {}

    result = dict(row)
    # Get main agent data separately
    main_query = """
        SELECT * FROM agent_response WHERE id = $1
    """
    main_row = await execute_async_single(conn, main_query, agent_response_id)
    if main_row:
        result["main_agent"] = dict(main_row)

    return result
