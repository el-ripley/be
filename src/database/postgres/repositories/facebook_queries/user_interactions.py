"""Queries for user interaction history."""

from typing import Any, Dict, List, Tuple

import asyncpg

from src.database.postgres.executor import execute_async_query


async def get_user_comments_by_psid(
    conn: asyncpg.Connection,
    psid: str,
    page_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Get all comments by a user (PSID) on a specific page.

    Args:
        conn: Database connection
        psid: Page-scoped user ID
        page_id: Facebook page ID
        limit: Max comments to return

    Returns:
        List of comment records
    """
    limit = max(1, min(limit, 100))

    query = """
        SELECT 
            c.id,
            c.post_id,
            c.parent_comment_id,
            c.is_from_page,
            c.fan_page_id,
            c.message,
            c.photo_url,
            c.video_url,
            c.facebook_created_time,
            c.like_count,
            c.reply_count,
            c.created_at,
            c.updated_at,
            p.message AS post_message
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE c.facebook_page_scope_user_id = $1
          AND c.fan_page_id = $2
          AND c.deleted_at IS NULL
        ORDER BY c.facebook_created_time DESC NULLS LAST, c.created_at DESC
        LIMIT $3
    """

    return await execute_async_query(conn, query, psid, page_id, limit)


async def get_user_messages_by_psid(
    conn: asyncpg.Connection,
    psid: str,
    page_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Get all messages from a user (PSID) in conversations with a specific page.

    Args:
        conn: Database connection
        psid: Page-scoped user ID
        page_id: Facebook page ID
        limit: Max messages to return

    Returns:
        List of message records
    """
    limit = max(1, min(limit, 100))

    query = """
        SELECT 
            m.id,
            m.conversation_id,
            m.is_echo,
            m.text,
            m.photo_url,
            m.video_url,
            m.audio_url,
            m.template_data,
            m.facebook_timestamp,
            m.created_at,
            m.updated_at
        FROM messages m
        JOIN facebook_conversation_messages fcm ON m.conversation_id = fcm.id
        WHERE fcm.facebook_page_scope_user_id = $1
          AND fcm.fan_page_id = $2
          AND m.deleted_at IS NULL
          AND fcm.deleted_at IS NULL
          AND m.is_echo = FALSE
        ORDER BY m.facebook_timestamp DESC NULLS LAST, m.created_at DESC
        LIMIT $3
    """

    return await execute_async_query(conn, query, psid, page_id, limit)


async def get_user_post_reactions_by_psid(
    conn: asyncpg.Connection,
    psid: str,
    page_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Get all post reactions by a user (PSID) on posts from a specific page.

    Args:
        conn: Database connection
        psid: Page-scoped user ID
        page_id: Facebook page ID
        limit: Max reactions to return

    Returns:
        List of post reaction records
    """
    limit = max(1, min(limit, 100))

    query = """
        SELECT 
            pr.id,
            pr.post_id,
            pr.fan_page_id,
            pr.reactor_id,
            pr.reactor_name,
            pr.reactor_profile_pic,
            pr.reaction_type,
            pr.created_at,
            pr.updated_at,
            p.message AS post_message
        FROM post_reactions pr
        JOIN posts p ON pr.post_id = p.id
        WHERE pr.reactor_id = $1
          AND pr.fan_page_id = $2
        ORDER BY pr.created_at DESC
        LIMIT $3
    """

    return await execute_async_query(conn, query, psid, page_id, limit)


async def get_user_comment_reactions_by_psid(
    conn: asyncpg.Connection,
    psid: str,
    page_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Get all comment reactions by a user (PSID) on comments from a specific page.

    Args:
        conn: Database connection
        psid: Page-scoped user ID
        page_id: Facebook page ID
        limit: Max reactions to return

    Returns:
        List of comment reaction records
    """
    limit = max(1, min(limit, 100))

    query = """
        SELECT 
            cr.id,
            cr.comment_id,
            cr.post_id,
            cr.fan_page_id,
            cr.reactor_id,
            cr.reactor_name,
            cr.reaction_type,
            cr.created_at,
            cr.updated_at,
            c.message AS comment_message
        FROM comment_reactions cr
        JOIN comments c ON cr.comment_id = c.id
        WHERE cr.reactor_id = $1
          AND cr.fan_page_id = $2
        ORDER BY cr.created_at DESC
        LIMIT $3
    """

    return await execute_async_query(conn, query, psid, page_id, limit)


# ===== NEW: COUNT QUERIES FOR SUMMARY =====


async def count_user_comments(
    conn: asyncpg.Connection,
    psid: str,
    page_id: str,
) -> int:
    """Count total comments by a user on a specific page."""
    query = """
        SELECT COUNT(*) as count
        FROM comments c
        WHERE c.facebook_page_scope_user_id = $1
          AND c.fan_page_id = $2
          AND c.deleted_at IS NULL
    """
    result = await execute_async_query(conn, query, psid, page_id)
    return result[0]["count"] if result else 0


async def count_user_post_reactions(
    conn: asyncpg.Connection,
    psid: str,
    page_id: str,
) -> int:
    """Count total post reactions by a user on a specific page."""
    query = """
        SELECT COUNT(*) as count
        FROM post_reactions pr
        WHERE pr.reactor_id = $1
          AND pr.fan_page_id = $2
    """
    result = await execute_async_query(conn, query, psid, page_id)
    return result[0]["count"] if result else 0


async def count_user_comment_reactions(
    conn: asyncpg.Connection,
    psid: str,
    page_id: str,
) -> int:
    """Count total comment reactions by a user on a specific page."""
    query = """
        SELECT COUNT(*) as count
        FROM comment_reactions cr
        WHERE cr.reactor_id = $1
          AND cr.fan_page_id = $2
    """
    result = await execute_async_query(conn, query, psid, page_id)
    return result[0]["count"] if result else 0


# ===== NEW: MINIMAL FIELD QUERIES FOR AGENT DISCOVERY =====


async def get_user_comments_minimal(
    conn: asyncpg.Connection,
    psid: str,
    page_id: str,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Get minimal comment info for agent discovery with pagination.

    Returns: (comments, total_count)
    Each comment contains: comment_id, post_id, conversation_id, message, created_at, is_reply
    """
    limit = max(1, min(limit, 100))

    # Get total count
    count_result = await count_user_comments(conn, psid, page_id)

    # Get paginated comments with conversation_id
    # Fixed: Join through facebook_conversation_comment_entries to get conversation_id
    query = """
        SELECT 
            c.id as comment_id,
            c.post_id,
            c.message,
            c.facebook_created_time as created_at,
            c.parent_comment_id IS NOT NULL as is_reply,
            fcce.conversation_id::text as conversation_id
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        LEFT JOIN facebook_conversation_comment_entries fcce ON fcce.comment_id = c.id
        WHERE c.facebook_page_scope_user_id = $1
          AND c.fan_page_id = $2
          AND c.deleted_at IS NULL
        ORDER BY c.facebook_created_time DESC NULLS LAST, c.created_at DESC
        LIMIT $3 OFFSET $4
    """

    comments = await execute_async_query(conn, query, psid, page_id, limit, offset)

    return comments, count_result


async def get_user_post_reactions_minimal(
    conn: asyncpg.Connection,
    psid: str,
    page_id: str,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Get minimal post reaction info for agent discovery with pagination.

    Returns: (reactions, total_count)
    Each reaction contains: reaction_type, created_at, post_id
    """
    limit = max(1, min(limit, 100))

    # Get total count
    count_result = await count_user_post_reactions(conn, psid, page_id)

    # Get paginated reactions
    query = """
        SELECT 
            pr.reaction_type,
            pr.created_at,
            pr.post_id
        FROM post_reactions pr
        WHERE pr.reactor_id = $1
          AND pr.fan_page_id = $2
        ORDER BY pr.created_at DESC
        LIMIT $3 OFFSET $4
    """

    reactions = await execute_async_query(conn, query, psid, page_id, limit, offset)
    return reactions, count_result


async def get_user_comment_reactions_minimal(
    conn: asyncpg.Connection,
    psid: str,
    page_id: str,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Get minimal comment reaction info for agent discovery with pagination.

    Returns: (reactions, total_count)
    Each reaction contains: reaction_type, created_at, post_id, comment_id
    """
    limit = max(1, min(limit, 100))

    # Get total count
    count_result = await count_user_comment_reactions(conn, psid, page_id)

    # Get paginated reactions
    query = """
        SELECT 
            cr.reaction_type,
            cr.created_at,
            cr.post_id,
            cr.comment_id
        FROM comment_reactions cr
        WHERE cr.reactor_id = $1
          AND cr.fan_page_id = $2
        ORDER BY cr.created_at DESC
        LIMIT $3 OFFSET $4
    """

    reactions = await execute_async_query(conn, query, psid, page_id, limit, offset)
    return reactions, count_result
