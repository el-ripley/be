from typing import Any, Dict, List, Optional

import asyncpg

from src.database.postgres.executor import execute_async_query, execute_async_returning
from src.database.postgres.utils import get_current_timestamp


async def upsert_post_reactions(
    conn: asyncpg.Connection,
    post_id: str,
    fan_page_id: str,
    reactions_list: List[Dict[str, Any]],
) -> int:
    """
    Upsert reactions for a post.

    Args:
        conn: Database connection
        post_id: Post ID
        fan_page_id: Fan page ID
        reactions_list: List of reaction dicts with keys:
            - id: Page-scoped user ID (PSID) if user reaction, None if page reaction
            - name: User/Page display name, None if page reaction
            - type: Reaction type (LIKE, LOVE, HAHA, etc.)
            - profile_pic: Optional profile picture URL

    Returns:
        Number of reactions upserted

    Note:
        If reaction id is None, it means the page itself reacted (use fan_page_id to get page info)
    """
    if not reactions_list:
        return 0

    current_time = get_current_timestamp()
    values_placeholders = []
    all_params: List[Any] = []
    param_index = 1

    for reaction in reactions_list:
        placeholders = []
        for _ in range(8):  # 8 columns in INSERT
            placeholders.append(f"${param_index}")
            param_index += 1
        values_placeholders.append(f"({', '.join(placeholders)})")
        params_for_reaction = [
            post_id,
            fan_page_id,
            reaction.get("id"),  # reactor_id
            reaction.get("name"),  # reactor_name
            reaction.get("profile_pic"),  # reactor_profile_pic (optional)
            reaction.get("type"),  # reaction_type
            current_time,  # created_at
            current_time,  # updated_at
        ]
        all_params.extend(params_for_reaction)

    query = f"""
        INSERT INTO post_reactions (
            post_id, fan_page_id, reactor_id, reactor_name, reactor_profile_pic,
            reaction_type, created_at, updated_at
        )
        VALUES {', '.join(values_placeholders)}
        ON CONFLICT (post_id, reactor_id) DO UPDATE SET
            reactor_name = EXCLUDED.reactor_name,
            reactor_profile_pic = EXCLUDED.reactor_profile_pic,
            reaction_type = EXCLUDED.reaction_type,
            updated_at = EXCLUDED.updated_at
        RETURNING id
    """

    rows = await execute_async_query(conn, query, *all_params)
    return len(rows)


async def upsert_comment_reactions(
    conn: asyncpg.Connection,
    comment_id: str,
    post_id: str,
    fan_page_id: str,
    reactions_list: List[Dict[str, Any]],
) -> int:
    """
    Upsert reactions for a comment.

    Args:
        conn: Database connection
        comment_id: Comment ID
        post_id: Post ID
        fan_page_id: Fan page ID
        reactions_list: List of reaction dicts with keys:
            - id: Page-scoped user ID (PSID) if user reaction, None if page reaction
            - name: User/Page display name, None if page reaction
            - type: Reaction type (LIKE, LOVE, HAHA, etc.)

    Returns:
        Number of reactions upserted

    Note:
        If reaction id is None, it means the page itself reacted (use fan_page_id to get page info)
    """
    if not reactions_list:
        return 0

    current_time = get_current_timestamp()
    values_placeholders = []
    all_params: List[Any] = []
    param_index = 1

    for reaction in reactions_list:
        placeholders = []
        for _ in range(8):  # Fixed: 8 columns in INSERT
            placeholders.append(f"${param_index}")
            param_index += 1
        values_placeholders.append(f"({', '.join(placeholders)})")
        all_params.extend(
            [
                comment_id,
                post_id,
                fan_page_id,
                reaction.get("id"),  # reactor_id
                reaction.get("name"),  # reactor_name
                reaction.get("type"),  # reaction_type
                current_time,  # created_at
                current_time,  # updated_at
            ]
        )

    query = f"""
        INSERT INTO comment_reactions (
            comment_id, post_id, fan_page_id, reactor_id, reactor_name,
            reaction_type, created_at, updated_at
        )
        VALUES {', '.join(values_placeholders)}
        ON CONFLICT (comment_id, reactor_id) DO UPDATE SET
            reactor_name = EXCLUDED.reactor_name,
            reaction_type = EXCLUDED.reaction_type,
            updated_at = EXCLUDED.updated_at
        RETURNING id
    """

    rows = await execute_async_query(conn, query, *all_params)
    return len(rows)


async def get_post_reactions(
    conn: asyncpg.Connection,
    post_id: str,
) -> List[Dict[str, Any]]:
    """Get all reactions for a post."""
    query = """
        SELECT * FROM post_reactions
        WHERE post_id = $1
        ORDER BY created_at DESC
    """
    return await execute_async_query(conn, query, post_id)


async def get_comment_reactions(
    conn: asyncpg.Connection,
    comment_id: str,
) -> List[Dict[str, Any]]:
    """Get all reactions for a comment."""
    query = """
        SELECT * FROM comment_reactions
        WHERE comment_id = $1
        ORDER BY created_at DESC
    """
    return await execute_async_query(conn, query, comment_id)


async def update_post_engagement(
    conn: asyncpg.Connection,
    post_id: str,
    reaction_total_count: Optional[int] = None,
    reaction_like_count: Optional[int] = None,
    reaction_love_count: Optional[int] = None,
    reaction_haha_count: Optional[int] = None,
    reaction_wow_count: Optional[int] = None,
    reaction_sad_count: Optional[int] = None,
    reaction_angry_count: Optional[int] = None,
    reaction_care_count: Optional[int] = None,
    share_count: Optional[int] = None,
    comment_count: Optional[int] = None,
    full_picture: Optional[str] = None,
    permalink_url: Optional[str] = None,
    status_type: Optional[str] = None,
    is_published: Optional[bool] = None,
    reactions_fetched_at: Optional[int] = None,
    engagement_fetched_at: Optional[int] = None,
) -> Dict[str, Any]:
    """Update post engagement data."""
    current_time = get_current_timestamp()

    # Build dynamic update query
    updates = []
    params: List[Any] = []
    param_index = 1

    if reaction_total_count is not None:
        updates.append(f"reaction_total_count = ${param_index}")
        params.append(reaction_total_count)
        param_index += 1
    if reaction_like_count is not None:
        updates.append(f"reaction_like_count = ${param_index}")
        params.append(reaction_like_count)
        param_index += 1
    if reaction_love_count is not None:
        updates.append(f"reaction_love_count = ${param_index}")
        params.append(reaction_love_count)
        param_index += 1
    if reaction_haha_count is not None:
        updates.append(f"reaction_haha_count = ${param_index}")
        params.append(reaction_haha_count)
        param_index += 1
    if reaction_wow_count is not None:
        updates.append(f"reaction_wow_count = ${param_index}")
        params.append(reaction_wow_count)
        param_index += 1
    if reaction_sad_count is not None:
        updates.append(f"reaction_sad_count = ${param_index}")
        params.append(reaction_sad_count)
        param_index += 1
    if reaction_angry_count is not None:
        updates.append(f"reaction_angry_count = ${param_index}")
        params.append(reaction_angry_count)
        param_index += 1
    if reaction_care_count is not None:
        updates.append(f"reaction_care_count = ${param_index}")
        params.append(reaction_care_count)
        param_index += 1
    if share_count is not None:
        updates.append(f"share_count = ${param_index}")
        params.append(share_count)
        param_index += 1
    if comment_count is not None:
        updates.append(f"comment_count = ${param_index}")
        params.append(comment_count)
        param_index += 1
    if full_picture is not None:
        updates.append(f"full_picture = ${param_index}")
        params.append(full_picture)
        param_index += 1
    if permalink_url is not None:
        updates.append(f"permalink_url = ${param_index}")
        params.append(permalink_url)
        param_index += 1
    if status_type is not None:
        updates.append(f"status_type = ${param_index}")
        params.append(status_type)
        param_index += 1
    if is_published is not None:
        updates.append(f"is_published = ${param_index}")
        params.append(is_published)
        param_index += 1
    if reactions_fetched_at is not None:
        updates.append(f"reactions_fetched_at = ${param_index}")
        params.append(reactions_fetched_at)
        param_index += 1
    if engagement_fetched_at is not None:
        updates.append(f"engagement_fetched_at = ${param_index}")
        params.append(engagement_fetched_at)
        param_index += 1

    if not updates:
        # No updates, just return current post
        from src.database.postgres.repositories.facebook_queries.comments.comment_posts import (
            get_post_by_id,
        )

        result = await get_post_by_id(conn, post_id)
        return result or {}

    updates.append(f"updated_at = ${param_index}")
    params.append(current_time)
    param_index += 1

    params.append(post_id)  # WHERE clause

    query = f"""
        UPDATE posts
        SET {', '.join(updates)}
        WHERE id = ${param_index}
        RETURNING *
    """

    return await execute_async_returning(conn, query, *params)


async def update_comment_engagement(
    conn: asyncpg.Connection,
    comment_id: str,
    like_count: Optional[int] = None,
    reply_count: Optional[int] = None,
    reactions_fetched_at: Optional[int] = None,
) -> Dict[str, Any]:
    """Update comment engagement data."""
    current_time = get_current_timestamp()

    updates = []
    params: List[Any] = []
    param_index = 1

    if like_count is not None:
        updates.append(f"like_count = ${param_index}")
        params.append(like_count)
        param_index += 1
    if reply_count is not None:
        updates.append(f"reply_count = ${param_index}")
        params.append(reply_count)
        param_index += 1
    if reactions_fetched_at is not None:
        updates.append(f"reactions_fetched_at = ${param_index}")
        params.append(reactions_fetched_at)
        param_index += 1

    if not updates:
        from src.database.postgres.repositories.facebook_queries.comments.comment_records import (
            get_comment,
        )

        result = await get_comment(conn, comment_id)
        return result or {}

    updates.append(f"updated_at = ${param_index}")
    params.append(current_time)
    param_index += 1

    params.append(comment_id)

    query = f"""
        UPDATE comments
        SET {', '.join(updates)}
        WHERE id = ${param_index}
        RETURNING *
    """

    return await execute_async_returning(conn, query, *params)
