from typing import Optional, Dict, Any, List
from decimal import Decimal
import asyncpg
from src.database.postgres.executor import (
    execute_async_single,
    execute_async_returning,
    execute_async_query,
)
from src.database.postgres.utils import generate_uuid, get_current_timestamp


# ================================================================
# FAN PAGE OPERATIONS
# ================================================================


async def get_page_by_id(
    conn: asyncpg.Connection, page_id: str
) -> Optional[Dict[str, Any]]:
    """Get a Facebook page by ID."""
    query = "SELECT * FROM fan_pages WHERE id = $1"
    return await execute_async_single(conn, query, page_id)


async def get_pages_by_ids_with_fields(
    conn: asyncpg.Connection,
    page_ids: List[str],
    fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Get multiple Facebook pages by IDs, optionally selecting specific fields.

    Args:
        conn: Database connection
        page_ids: List of page IDs to fetch
        fields: Optional list of field names to select. If None, selects all fields.
                Valid fields: id, name, avatar, category, fan_count, followers_count,
                rating_count, overall_star_rating, about, description, link, website,
                phone, emails, location, cover, hours, is_verified, created_at, updated_at

    Returns:
        List of page dictionaries with selected fields
    """
    if not page_ids:
        return []

    # Valid fields in fan_pages table
    valid_fields = {
        "id",
        "name",
        "avatar",
        "category",
        "fan_count",
        "followers_count",
        "rating_count",
        "overall_star_rating",
        "about",
        "description",
        "link",
        "website",
        "phone",
        "emails",
        "location",
        "cover",
        "hours",
        "is_verified",
        "created_at",
        "updated_at",
    }

    # If fields specified, validate and use them; otherwise select all
    if fields:
        # Filter to only valid fields and always include id
        selected_fields = ["id"] + [
            f for f in fields if f in valid_fields and f != "id"
        ]
        # Remove duplicates while preserving order
        seen = set()
        selected_fields = [f for f in selected_fields if not (f in seen or seen.add(f))]
        field_list = ", ".join(selected_fields)
    else:
        field_list = "*"

    query = f"SELECT {field_list} FROM fan_pages WHERE id = ANY($1) ORDER BY name"
    results = await execute_async_query(conn, query, page_ids)

    # Convert Decimal to float for JSON serialization
    for result in results:
        if "overall_star_rating" in result and isinstance(
            result["overall_star_rating"], Decimal
        ):
            result["overall_star_rating"] = float(result["overall_star_rating"])

    return results


async def create_fan_page(
    conn: asyncpg.Connection,
    page_id: str,
    name: Optional[str] = None,
    avatar: Optional[str] = None,
    category: Optional[str] = None,
    fan_count: Optional[int] = None,
    followers_count: Optional[int] = None,
    rating_count: Optional[int] = None,
    overall_star_rating: Optional[float] = None,
    about: Optional[str] = None,
    description: Optional[str] = None,
    link: Optional[str] = None,
    website: Optional[str] = None,
    phone: Optional[str] = None,
    emails: Optional[Dict[str, Any]] = None,
    location: Optional[Dict[str, Any]] = None,
    cover: Optional[str] = None,
    hours: Optional[Dict[str, Any]] = None,
    is_verified: Optional[bool] = None,
    created_at: Optional[int] = None,
    updated_at: Optional[int] = None,
) -> str:
    """Create a new fan page."""
    current_time = get_current_timestamp()

    # Convert dicts to JSON strings for JSONB fields
    import json

    emails_json = json.dumps(emails) if emails else None
    location_json = json.dumps(location) if location else None
    hours_json = json.dumps(hours) if hours else None

    query = """
        INSERT INTO fan_pages (
            id, name, avatar, category, fan_count, followers_count, rating_count,
            overall_star_rating, about, description, link, website, phone,
            emails, location, cover, hours, is_verified, created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            avatar = EXCLUDED.avatar,
            category = EXCLUDED.category,
            fan_count = EXCLUDED.fan_count,
            followers_count = EXCLUDED.followers_count,
            rating_count = EXCLUDED.rating_count,
            overall_star_rating = EXCLUDED.overall_star_rating,
            about = EXCLUDED.about,
            description = EXCLUDED.description,
            link = EXCLUDED.link,
            website = EXCLUDED.website,
            phone = EXCLUDED.phone,
            emails = EXCLUDED.emails,
            location = EXCLUDED.location,
            cover = EXCLUDED.cover,
            hours = EXCLUDED.hours,
            is_verified = EXCLUDED.is_verified,
            updated_at = EXCLUDED.updated_at
        RETURNING id
    """

    result = await execute_async_returning(
        conn,
        query,
        page_id,
        name,
        avatar,
        category,
        fan_count,
        followers_count,
        rating_count,
        overall_star_rating,
        about,
        description,
        link,
        website,
        phone,
        emails_json,
        location_json,
        cover,
        hours_json,
        is_verified,
        created_at or current_time,
        updated_at or current_time,
    )
    return result["id"]


# ================================================================
# FACEBOOK APP SCOPE USER OPERATIONS (ASID)
# ================================================================


async def create_facebook_app_scope_user(
    conn: asyncpg.Connection,
    facebook_user_id: str,  # ASID
    user_id: str,
    name: Optional[str] = None,
    gender: Optional[str] = None,
    email: Optional[str] = None,
    picture: Optional[str] = None,
    created_at: Optional[int] = None,
    updated_at: Optional[int] = None,
) -> str:
    """Create a new Facebook app scope user."""
    current_time = get_current_timestamp()

    query = """
        INSERT INTO facebook_app_scope_users (id, user_id, name, gender, email, picture, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id
    """

    result = await execute_async_returning(
        conn,
        query,
        facebook_user_id,
        user_id,
        name,
        gender,
        email,
        picture,
        created_at or current_time,
        updated_at or current_time,
    )
    return result["id"]


async def get_facebook_app_scope_user_by_id(
    conn: asyncpg.Connection, facebook_user_id: str
) -> Optional[Dict[str, Any]]:
    """Get a Facebook app scope user by ASID with joined user information."""
    query = """
        SELECT 
            fasu.id,
            fasu.user_id,
            fasu.name,
            fasu.gender,
            fasu.email,
            fasu.picture,
            fasu.created_at AS fasu_created_at,
            fasu.updated_at AS fasu_updated_at,
            u.created_at AS user_created_at,
            u.updated_at AS user_updated_at
        FROM facebook_app_scope_users fasu
        INNER JOIN users u ON fasu.user_id = u.id
        WHERE fasu.id = $1
    """
    return await execute_async_single(conn, query, facebook_user_id)


async def update_facebook_app_scope_user(
    conn: asyncpg.Connection,
    facebook_user_id: str,  # ASID
    name: Optional[str] = None,
    gender: Optional[str] = None,
    email: Optional[str] = None,
    picture: Optional[str] = None,
    updated_at: Optional[int] = None,
) -> str:
    """Update a Facebook app scope user profile data."""
    current_time = get_current_timestamp()

    query = """
        UPDATE facebook_app_scope_users 
        SET name = $2, gender = $3, email = $4, picture = $5, updated_at = $6
        WHERE id = $1
        RETURNING id
    """

    result = await execute_async_returning(
        conn,
        query,
        facebook_user_id,
        name,
        gender,
        email,
        picture,
        updated_at or current_time,
    )
    return result["id"]


# ================================================================
# FACEBOOK PAGE ADMIN OPERATIONS
# ================================================================


async def create_facebook_page_admin(
    conn: asyncpg.Connection,
    facebook_user_id: str,
    page_id: str,
    access_token: str,
    tasks: Optional[Dict[str, Any]] = None,
    created_at: Optional[int] = None,
    updated_at: Optional[int] = None,
) -> str:
    """Create a new Facebook page admin relationship."""
    current_time = get_current_timestamp()
    admin_id = generate_uuid()

    # Convert tasks dict to JSON string if provided
    tasks_json = None
    if tasks:
        import json

        tasks_json = json.dumps(tasks)

    query = """
        INSERT INTO facebook_page_admins (id, facebook_user_id, page_id, access_token, tasks, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (facebook_user_id, page_id) DO UPDATE SET
            access_token = EXCLUDED.access_token,
            tasks = EXCLUDED.tasks,
            updated_at = EXCLUDED.updated_at
        RETURNING id
    """

    result = await execute_async_returning(
        conn,
        query,
        admin_id,
        facebook_user_id,
        page_id,
        access_token,
        tasks_json,
        created_at or current_time,
        updated_at or current_time,
    )
    return result["id"]


async def get_facebook_page_admins_by_page(
    conn: asyncpg.Connection, page_id: str
) -> list:
    """Get all page admins for a Facebook page with corresponding user information."""
    query = """
        SELECT 
            fpa.id,
            fpa.facebook_user_id,
            fpa.page_id,
            fpa.access_token,
            fpa.tasks,
            fpa.created_at AS admin_created_at,
            fpa.updated_at AS admin_updated_at,
            fasu.user_id,
            fasu.name AS facebook_name,
            fasu.gender,
            fasu.email,
            fasu.picture,
            fasu.created_at AS facebook_user_created_at,
            fasu.updated_at AS facebook_user_updated_at,
            u.created_at AS user_created_at,
            u.updated_at AS user_updated_at
        FROM facebook_page_admins fpa
        INNER JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
        INNER JOIN users u ON fasu.user_id = u.id
        WHERE fpa.page_id = $1
        ORDER BY fpa.updated_at DESC
    """

    return await execute_async_query(conn, query, page_id)


async def get_facebook_page_admins_by_user_id(
    conn: asyncpg.Connection, user_id: str
) -> List[Dict[str, Any]]:
    """
    Get all Facebook page admin records for a specific user.

    Args:
        conn: Database connection
        user_id: Internal user ID

    Returns:
        List of page admin records with page information
    """
    query = """
        SELECT 
            fpa.id,
            fpa.facebook_user_id,
            fpa.page_id,
            fpa.access_token,
            fpa.tasks,
            fpa.created_at,
            fpa.updated_at,
            fp.name as page_name,
            fp.avatar as page_avatar,
            fp.category as page_category
        FROM facebook_page_admins fpa
        JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
        JOIN fan_pages fp ON fpa.page_id = fp.id
        WHERE fasu.user_id = $1
        ORDER BY fp.name ASC
    """

    return await execute_async_query(conn, query, user_id)


# ================================================================
# FACEBOOK PAGE SCOPE USER OPERATIONS (PSID)
# ================================================================


async def get_facebook_page_scope_user_by_id(
    conn: asyncpg.Connection, psid: str
) -> Optional[Dict[str, Any]]:
    """Get a Facebook page scope user by PSID."""
    query = "SELECT * FROM facebook_page_scope_users WHERE id = $1"
    return await execute_async_single(conn, query, psid)


async def upsert_facebook_page_scope_user(
    conn: asyncpg.Connection,
    psid: str,
    fan_page_id: str,
    user_info: Optional[Dict[str, Any]] = None,
    created_at: Optional[int] = None,
    updated_at: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Create or update a Facebook page scope user and return the full record."""
    current_time = get_current_timestamp()

    # Convert user_info dict to JSON string if provided
    user_info_json = None
    if user_info:
        import json

        user_info_json = json.dumps(user_info)

    query = """
        INSERT INTO facebook_page_scope_users (id, fan_page_id, user_info, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (id) DO UPDATE SET
            user_info = EXCLUDED.user_info,
            updated_at = EXCLUDED.updated_at
        RETURNING *
    """

    result = await execute_async_returning(
        conn,
        query,
        psid,
        fan_page_id,
        user_info_json,
        created_at or current_time,
        updated_at or current_time,
    )
    return result


async def get_facebook_page_scope_users_by_page_ids(
    conn: asyncpg.Connection,
    page_ids: List[str],
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> tuple[List[Dict[str, Any]], int]:
    """
    Get page scope users for given page IDs with pagination.
    
    Returns:
        Tuple of (users list, total count)
    """
    if not page_ids:
        return [], 0

    # Get total count
    count_query = """
        SELECT COUNT(*) as total
        FROM facebook_page_scope_users fpsu
        WHERE fpsu.fan_page_id = ANY($1::text[])
    """
    count_result = await execute_async_single(conn, count_query, page_ids)
    total = count_result.get("total", 0) if count_result else 0

    # Get paginated results
    params: List[Any] = [page_ids]
    param_idx = 2
    
    query = """
        SELECT 
            fpsu.id,
            fpsu.fan_page_id,
            fpsu.user_info,
            fpsu.created_at,
            fpsu.updated_at
        FROM facebook_page_scope_users fpsu
        WHERE fpsu.fan_page_id = ANY($1::text[])
        ORDER BY fpsu.fan_page_id, fpsu.updated_at DESC
    """
    
    if limit is not None:
        query += f" LIMIT ${param_idx}"
        params.append(limit)
        param_idx += 1
    
    if offset is not None:
        query += f" OFFSET ${param_idx}"
        params.append(offset)
    
    users = await execute_async_query(conn, query, *params)
    return users, total
