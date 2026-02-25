"""
Minimal user-related SQL query functions.

Basic CRUD operations for users and roles.
More complex queries will be added as needed based on business requirements.
"""

from typing import Optional, Dict, Any
import asyncpg
from ..executor import (
    execute_async_single,
    execute_async_returning,
    execute_async_query,
)
from ..utils import generate_uuid, get_current_timestamp, get_current_timestamp_ms


# ================================================================
# USER OPERATIONS
# ================================================================


async def create_user(
    conn: asyncpg.Connection,
    created_at: Optional[int] = None,
    updated_at: Optional[int] = None,
) -> str:
    """Create a new user."""
    current_time = get_current_timestamp()
    user_id = generate_uuid()

    query = """
        INSERT INTO users (id, created_at, updated_at)
        VALUES ($1, $2, $3)
        RETURNING id
    """

    result = await execute_async_returning(
        conn, query, user_id, created_at or current_time, updated_at or current_time
    )
    return result["id"]


async def get_user_by_id(
    conn: asyncpg.Connection, user_id: str
) -> Optional[Dict[str, Any]]:
    """Get a user by ID."""
    query = "SELECT * FROM users WHERE id = $1"
    return await execute_async_single(conn, query, user_id)


# ================================================================
# USER ROLE OPERATIONS
# ================================================================


async def get_user_roles(
    conn: asyncpg.Connection, user_id: str
) -> list[Dict[str, Any]]:
    """Get all roles for a user."""
    query = """
        SELECT r.id, r.name
        FROM roles r
        JOIN user_role ur ON r.id = ur.role_id
        WHERE ur.user_id = $1
    """
    return await execute_async_query(conn, query, user_id)


async def assign_role_to_user_by_name(
    conn: asyncpg.Connection, user_id: str, role_name: str
) -> bool:
    """Assign a role to a user by role name."""
    query = """
        INSERT INTO user_role (user_id, role_id)
        SELECT $1, r.id
        FROM roles r
        WHERE r.name = $2
        ON CONFLICT (user_id, role_id) DO NOTHING
    """
    try:
        await execute_async_single(conn, query, user_id, role_name)
        return True
    except Exception:
        return False


async def get_user_with_roles(
    conn: asyncpg.Connection, user_id: str
) -> Optional[Dict[str, Any]]:
    """Get a user with their roles."""
    user = await get_user_by_id(conn, user_id)
    if not user:
        return None

    roles = await get_user_roles(conn, user_id)
    user["roles"] = roles
    return user


async def get_comprehensive_user_info(
    conn: asyncpg.Connection, user_id: str
) -> Optional[Dict[str, Any]]:
    """
    Get comprehensive user information including all related data:
    - User basic info
    - User roles
    - Facebook app scope user info
    - Facebook page admin relationships with fan page details
    """

    # First check if user exists
    user = await get_user_by_id(conn, user_id)
    if not user:
        return None

    # Get user roles
    roles_query = """
        SELECT r.id, r.name
        FROM roles r
        JOIN user_role ur ON r.id = ur.role_id
        WHERE ur.user_id = $1
    """
    roles = await execute_async_query(conn, roles_query, user_id)

    # Get Facebook app scope user info
    facebook_user_query = """
        SELECT id, user_id, name, gender, email, picture, created_at, updated_at
        FROM facebook_app_scope_users
        WHERE user_id = $1
    """
    facebook_user = await execute_async_single(conn, facebook_user_query, user_id)

    # Get Facebook page admin relationships with fan page details
    page_admin_query = """
        SELECT 
            fpa.id as admin_id,
            fpa.facebook_user_id,
            fpa.page_id,
            fpa.access_token,
            fpa.tasks,
            fpa.created_at as admin_created_at,
            fpa.updated_at as admin_updated_at,
            fp.name as page_name,
            fp.avatar as page_avatar,
            fp.category as page_category,
            fp.created_at as page_created_at,
            fp.updated_at as page_updated_at
        FROM facebook_page_admins fpa
        JOIN fan_pages fp ON fpa.page_id = fp.id
        JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
        WHERE fasu.user_id = $1
        ORDER BY fpa.created_at DESC
    """
    page_admins = await execute_async_query(conn, page_admin_query, user_id)

    # Structure the response
    result = {
        "user": user,
        "roles": roles,
        "facebook_user": facebook_user,
        "page_admins": [],
    }

    # Format page admin data
    for admin in page_admins:
        admin_data = {
            "admin_id": admin["admin_id"],
            "facebook_user_id": admin["facebook_user_id"],
            "access_token": admin["access_token"],
            "tasks": admin["tasks"],
            "created_at": admin["admin_created_at"],
            "updated_at": admin["admin_updated_at"],
            "fan_page": {
                "id": admin["page_id"],
                "name": admin["page_name"],
                "avatar": admin["page_avatar"],
                "category": admin["page_category"],
                "created_at": admin["page_created_at"],
                "updated_at": admin["page_updated_at"],
            },
        }
        result["page_admins"].append(admin_data)

    return result


# ================================================================
# REFRESH TOKEN OPERATIONS
# ================================================================


async def create_refresh_token(
    conn: asyncpg.Connection,
    user_id: str,
    token: str,
    expires_at: int,
    created_at: Optional[int] = None,
    updated_at: Optional[int] = None,
) -> str:
    """Create a new refresh token."""
    current_time = get_current_timestamp()
    refresh_token_id = generate_uuid()

    query = """
        INSERT INTO refresh_tokens (id, user_id, token, expires_at, is_revoked, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
    """

    result = await execute_async_returning(
        conn,
        query,
        refresh_token_id,
        user_id,
        token,
        expires_at,
        False,  # is_revoked
        created_at or current_time,
        updated_at or current_time,
    )
    return result["id"]


async def get_refresh_token_by_token(
    conn: asyncpg.Connection, token: str
) -> Optional[Dict[str, Any]]:
    """Get a refresh token by token string."""
    query = "SELECT * FROM refresh_tokens WHERE token = $1 AND is_revoked = FALSE"
    return await execute_async_single(conn, query, token)


async def get_active_refresh_tokens_by_user_id(
    conn: asyncpg.Connection, user_id: str
) -> list[Dict[str, Any]]:
    """Get all active (non-revoked and non-expired) refresh tokens for a user."""
    current_time = get_current_timestamp()
    query = """
        SELECT * FROM refresh_tokens 
        WHERE user_id = $1 
        AND is_revoked = FALSE 
        AND expires_at > $2
        ORDER BY created_at DESC
    """
    return await execute_async_query(conn, query, user_id, current_time)


async def revoke_refresh_token(
    conn: asyncpg.Connection, token: str, updated_at: Optional[int] = None
) -> bool:
    """Revoke a specific refresh token."""
    current_time = get_current_timestamp()

    query = """
        UPDATE refresh_tokens 
        SET is_revoked = TRUE, updated_at = $2
        WHERE token = $1 AND is_revoked = FALSE
    """

    try:
        await execute_async_single(conn, query, token, updated_at or current_time)
        return True
    except Exception:
        return False


async def revoke_all_refresh_tokens_by_user_id(
    conn: asyncpg.Connection, user_id: str, updated_at: Optional[int] = None
) -> bool:
    """Revoke all refresh tokens for a user (useful for logout all devices)."""
    current_time = get_current_timestamp()

    query = """
        UPDATE refresh_tokens 
        SET is_revoked = TRUE, updated_at = $2
        WHERE user_id = $1 AND is_revoked = FALSE
    """

    try:
        await execute_async_single(conn, query, user_id, updated_at or current_time)
        return True
    except Exception:
        return False


async def delete_expired_refresh_tokens(conn: asyncpg.Connection) -> int:
    """Delete expired refresh tokens from database (cleanup function)."""
    current_time = get_current_timestamp()

    query = "DELETE FROM refresh_tokens WHERE expires_at <= $1"

    try:
        result = await conn.execute(query, current_time)
        # Extract number of deleted rows from result string like "DELETE 5"
        return int(result.split()[-1]) if result.split()[-1].isdigit() else 0
    except Exception:
        return 0


# ================================================================
# USER CONVERSATION SETTINGS OPERATIONS
# ================================================================


async def get_user_conversation_settings(
    conn: asyncpg.Connection, user_id: str
) -> Optional[Dict[str, Any]]:
    """Get user conversation settings for context management."""
    query = """
        SELECT 
            id, user_id, context_token_limit, context_buffer_percent,
            summarizer_model, vision_model, created_at, updated_at
        FROM user_conversation_settings
        WHERE user_id = $1
    """
    return await execute_async_single(conn, query, user_id)


async def upsert_user_conversation_settings(
    conn: asyncpg.Connection,
    user_id: str,
    context_token_limit: Optional[int] = None,
    context_buffer_percent: Optional[int] = None,
    summarizer_model: Optional[str] = None,
    vision_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create or update user conversation settings.

    Args:
        conn: Database connection
        user_id: User ID
        context_token_limit: Optional context token limit
        context_buffer_percent: Optional buffer percentage (0-100)
        summarizer_model: Optional summarizer model name
        vision_model: Optional vision model name

    Returns:
        Updated settings record
    """
    current_time = get_current_timestamp_ms()
    settings_id = generate_uuid()

    query = """
        INSERT INTO user_conversation_settings (
            id, user_id, context_token_limit, context_buffer_percent,
            summarizer_model, vision_model, created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (user_id) DO UPDATE SET
            context_token_limit = EXCLUDED.context_token_limit,
            context_buffer_percent = EXCLUDED.context_buffer_percent,
            summarizer_model = EXCLUDED.summarizer_model,
            vision_model = EXCLUDED.vision_model,
            updated_at = EXCLUDED.updated_at
        RETURNING id, user_id, context_token_limit, context_buffer_percent,
                  summarizer_model, vision_model, created_at, updated_at
    """

    return await execute_async_returning(
        conn,
        query,
        settings_id,
        user_id,
        context_token_limit,
        context_buffer_percent,
        summarizer_model,
        vision_model,
        current_time,
        current_time,
    )
