"""
Memory Blocks SQL query functions.
Handles CRUD operations for suggest_response memory blocks.
"""

from typing import Optional, Dict, Any, List
import asyncpg
from ..executor import (
    execute_async_single,
    execute_async_returning,
    execute_async_query,
    execute_async_command,
)
from ..utils import generate_uuid, get_current_timestamp_ms


# ================================================================
# MEMORY BLOCKS OPERATIONS
# ================================================================


async def get_latest_blocks(
    conn: asyncpg.Connection,
    prompt_type: str,
    prompt_id: str,
) -> List[Dict[str, Any]]:
    """
    Get all active blocks for a prompt (latest version of each block_key).
    Uses DISTINCT ON to get only the latest version per block_key,
    excluding blocks that have been removed (content = '__REMOVED__').
    """
    query = """
        SELECT DISTINCT ON (block_key)
            id, prompt_type, prompt_id, block_key, title, content,
            display_order, created_at, created_by_type
        FROM memory_blocks
        WHERE prompt_type = $1 AND prompt_id = $2
          AND NOT EXISTS (
            -- Exclude if there's a newer "tombstone" (removed) record
            SELECT 1 FROM memory_blocks t
            WHERE t.prompt_type = $1 
              AND t.prompt_id = $2 
              AND t.block_key = memory_blocks.block_key
              AND t.content = '__REMOVED__'
              AND t.created_at > memory_blocks.created_at
          )
          AND content != '__REMOVED__'
        ORDER BY block_key, created_at DESC
    """
    return await execute_async_query(conn, query, prompt_type, prompt_id)


async def get_block_by_key(
    conn: asyncpg.Connection,
    prompt_type: str,
    prompt_id: str,
    block_key: str,
) -> Optional[Dict[str, Any]]:
    """Get the latest version of a specific block by block_key."""
    query = """
        SELECT id, prompt_type, prompt_id, block_key, title, content,
               display_order, created_at, created_by_type
        FROM memory_blocks
        WHERE prompt_type = $1 
          AND prompt_id = $2 
          AND block_key = $3
          AND content != '__REMOVED__'
          AND NOT EXISTS (
            SELECT 1 FROM memory_blocks t
            WHERE t.prompt_type = $1 
              AND t.prompt_id = $2 
              AND t.block_key = $3
              AND t.content = '__REMOVED__'
              AND t.created_at > memory_blocks.created_at
          )
        ORDER BY created_at DESC
        LIMIT 1
    """
    return await execute_async_single(
        conn, query, prompt_type, prompt_id, block_key
    )


async def insert_block(
    conn: asyncpg.Connection,
    prompt_type: str,
    prompt_id: str,
    block_key: str,
    title: str,
    content: str,
    display_order: int,
    created_by_type: str,
) -> Dict[str, Any]:
    """Insert a new memory block (append-only pattern)."""
    block_id = generate_uuid()
    current_time = get_current_timestamp_ms()

    query = """
        INSERT INTO memory_blocks (
            id, prompt_type, prompt_id, block_key, title, content,
            display_order, created_at, created_by_type
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id, prompt_type, prompt_id, block_key, title, content,
                  display_order, created_at, created_by_type
    """
    return await execute_async_returning(
        conn,
        query,
        block_id,
        prompt_type,
        prompt_id,
        block_key,
        title,
        content,
        display_order,
        current_time,
        created_by_type,
    )


async def remove_block(
    conn: asyncpg.Connection,
    prompt_type: str,
    prompt_id: str,
    block_key: str,
    created_by_type: str,
) -> None:
    """
    Remove a block by inserting a tombstone record.
    Uses content = '__REMOVED__' to mark removal (append-only pattern).
    """
    await insert_block(
        conn,
        prompt_type,
        prompt_id,
        block_key,
        "__REMOVED__",  # title
        "__REMOVED__",  # content (tombstone marker)
        0,  # display_order (ignored for removed blocks)
        created_by_type,
    )


async def get_block_media(
    conn: asyncpg.Connection,
    block_ids: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Get media attachments for multiple blocks.
    Returns dict mapping block_id -> list of media items.
    """
    if not block_ids:
        return {}

    # Query to get media with block_id grouping
    query = """
        SELECT 
            mbm.block_id,
            mbm.media_id,
            mbm.display_order,
            ma.s3_url,
            ma.description
        FROM memory_block_media mbm
        JOIN media_assets ma ON mbm.media_id = ma.id
        WHERE mbm.block_id = ANY($1::uuid[])
        ORDER BY mbm.block_id, mbm.display_order
    """
    results = await execute_async_query(conn, query, block_ids)

    # Group by block_id
    media_map: Dict[str, List[Dict[str, Any]]] = {}
    for row in results:
        block_id = str(row["block_id"])
        if block_id not in media_map:
            media_map[block_id] = []
        media_map[block_id].append(
            {
                "media_id": str(row["media_id"]),
                "url": row["s3_url"],
                "description": row.get("description"),
                "display_order": row["display_order"],
            }
        )

    return media_map


async def link_media_to_block(
    conn: asyncpg.Connection,
    block_id: str,
    media_items: List[Dict[str, Any]],
) -> None:
    """
    Link media assets to a block.
    
    Args:
        block_id: Block ID to link media to
        media_items: List of dicts with {"media_id": str, "display_order": int}
    """
    if not media_items:
        return

    current_time = get_current_timestamp_ms()

    # Delete existing media links for this block
    delete_query = """
        DELETE FROM memory_block_media
        WHERE block_id = $1
    """
    await execute_async_command(conn, delete_query, block_id)

    # Insert new media links
    for item in media_items:
        media_id = item["media_id"]
        display_order = item.get("display_order", 1)
        link_id = generate_uuid()

        insert_query = """
            INSERT INTO memory_block_media (
                id, block_id, media_id, display_order, created_at
            ) VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (block_id, media_id) DO UPDATE SET
                display_order = EXCLUDED.display_order
        """
        await execute_async_command(
            conn, insert_query, link_id, block_id, media_id, display_order, current_time
        )


async def update_block_display_order(
    conn: asyncpg.Connection,
    prompt_type: str,
    prompt_id: str,
    block_key: str,
    new_display_order: int,
    created_by_type: str,
) -> None:
    """
    Update display_order for a block by inserting a new version.
    This is append-only, so we get the latest block and insert a new version
    with updated display_order.
    """
    # Get latest block
    latest_block = await get_block_by_key(conn, prompt_type, prompt_id, block_key)
    if not latest_block:
        raise ValueError(f"Block with key '{block_key}' not found")

    # Insert new version with updated display_order
    await insert_block(
        conn,
        prompt_type,
        prompt_id,
        block_key,
        latest_block["title"],
        latest_block["content"],
        new_display_order,
        created_by_type,
    )


async def copy_blocks_to_prompt(
    conn: asyncpg.Connection,
    source_prompt_type: str,
    source_prompt_id: str,
    target_prompt_type: str,
    target_prompt_id: str,
    created_by_type: str,
) -> List[Dict[str, Any]]:
    """
    Copy all blocks from source prompt to target prompt.
    Used for migrate_prompt operation.
    """
    # Get all blocks from source
    source_blocks = await get_latest_blocks(conn, source_prompt_type, source_prompt_id)

    copied_blocks = []
    for block in source_blocks:
        # Copy block
        new_block = await insert_block(
            conn,
            target_prompt_type,
            target_prompt_id,
            block["block_key"],
            block["title"],
            block["content"],
            block["display_order"],
            created_by_type,
        )
        copied_blocks.append(new_block)

        # Copy media for this block
        source_block_id = str(block["id"])
        media_map = await get_block_media(conn, [source_block_id])
        if source_block_id in media_map:
            media_items = [
                {
                    "media_id": m["media_id"],
                    "display_order": m["display_order"],
                }
                for m in media_map[source_block_id]
            ]
            await link_media_to_block(conn, str(new_block["id"]), media_items)

    return copied_blocks
