"""
Database utility functions for common operations.

This module provides utility functions for ID generation, timestamp handling,
and other common database operations.
"""

import time
from uuid import uuid4
from typing import Dict, Any, List, Optional


def generate_uuid() -> str:
    """
    Generate a random UUID string.

    Returns:
        str: UUID string in format like "123e4567-e89b-12d3-a456-426614174000"
    """
    return str(uuid4())


def get_current_timestamp() -> int:
    """
    Get current UNIX timestamp in seconds.

    Returns:
        int: Current timestamp as integer seconds since epoch
    """
    return int(time.time())


def get_current_timestamp_ms() -> int:
    """
    Get current UNIX timestamp in milliseconds.

    Returns:
        int: Current timestamp as integer milliseconds since epoch
    """
    return int(time.time() * 1000)


def build_insert_query(
    table: str, data: Dict[str, Any], on_conflict: Optional[str] = None
) -> tuple[str, Dict[str, Any]]:
    """
    Build a parameterized INSERT query from a data dictionary.

    Args:
        table: Table name
        data: Dictionary of column names and values
        on_conflict: Optional ON CONFLICT clause (e.g., "DO NOTHING" or "DO UPDATE SET ...")

    Returns:
        Tuple of (query_string, parameters_dict)

    Example:
        query, params = build_insert_query("users", {
            "id": "123...",
            "name": "John",
            "created_at": 1609459200
        })
        # query = "INSERT INTO users (id, name, created_at) VALUES (%(id)s, %(name)s, %(created_at)s)"
        # params = {"id": "123...", "name": "John", "created_at": 1609459200}
    """
    if not data:
        raise ValueError("Data dictionary cannot be empty")

    columns = list(data.keys())
    placeholders = [f"%({col})s" for col in columns]

    query = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
    )

    if on_conflict:
        query += f" ON CONFLICT {on_conflict}"

    return query, data


def build_update_query(
    table: str,
    data: Dict[str, Any],
    where_clause: str,
    where_params: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, Any]]:
    """
    Build a parameterized UPDATE query from a data dictionary.

    Args:
        table: Table name
        data: Dictionary of column names and new values
        where_clause: WHERE clause with named parameters
        where_params: Parameters for the WHERE clause

    Returns:
        Tuple of (query_string, parameters_dict)

    Example:
        query, params = build_update_query(
            "users",
            {"name": "John Doe", "updated_at": 1609459200},
            "id = %(user_id)s",
            {"user_id": "123..."}
        )
    """
    if not data:
        raise ValueError("Data dictionary cannot be empty")

    set_clauses = [f"{col} = %({col})s" for col in data.keys()]

    query = f"UPDATE {table} SET {', '.join(set_clauses)} WHERE {where_clause}"

    # Combine data parameters with where parameters
    all_params = dict(data)
    if where_params:
        all_params.update(where_params)

    return query, all_params


def build_select_query(
    table: str,
    columns: Optional[List[str]] = None,
    where_clause: Optional[str] = None,
    order_by: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> str:
    """
    Build a SELECT query with optional clauses.

    Args:
        table: Table name
        columns: List of column names to select (None for *)
        where_clause: WHERE clause with named parameters
        order_by: ORDER BY clause
        limit: LIMIT value
        offset: OFFSET value

    Returns:
        SQL query string

    Example:
        query = build_select_query(
            "users",
            columns=["id", "name", "created_at"],
            where_clause="created_at > %(min_date)s",
            order_by="created_at DESC",
            limit=10
        )
    """
    column_list = ", ".join(columns) if columns else "*"
    query = f"SELECT {column_list} FROM {table}"

    if where_clause:
        query += f" WHERE {where_clause}"

    if order_by:
        query += f" ORDER BY {order_by}"

    if limit is not None:
        query += f" LIMIT {limit}"

    if offset is not None:
        query += f" OFFSET {offset}"

    return query


def paginate_params(page: int = 1, page_size: int = 20) -> tuple[int, int]:
    """
    Calculate LIMIT and OFFSET values for pagination.

    Args:
        page: Page number (1-indexed)
        page_size: Number of items per page

    Returns:
        Tuple of (limit, offset)

    Example:
        limit, offset = paginate_params(page=3, page_size=10)
        # limit=10, offset=20
    """
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20

    offset = (page - 1) * page_size
    return page_size, offset


def ensure_required_fields(data: Dict[str, Any], required_fields: List[str]) -> None:
    """
    Ensure that required fields are present in the data dictionary.

    Args:
        data: Data dictionary to validate
        required_fields: List of required field names

    Raises:
        ValueError: If any required field is missing

    Example:
        ensure_required_fields(user_data, ["id", "created_at", "updated_at"])
    """
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        raise ValueError(f"Missing required fields: {', '.join(missing_fields)}")


def prepare_timestamps(
    data: Dict[str, Any], created: bool = True, updated: bool = True
) -> Dict[str, Any]:
    """
    Add timestamp fields to data dictionary if not present.

    Args:
        data: Data dictionary to modify
        created: Whether to add created_at if missing
        updated: Whether to add updated_at if missing

    Returns:
        Modified data dictionary

    Example:
        user_data = prepare_timestamps({"name": "John"})
        # Adds created_at and updated_at with current timestamp
    """
    result = dict(data)
    current_time = get_current_timestamp()

    if created and "created_at" not in result:
        result["created_at"] = current_time

    if updated and "updated_at" not in result:
        result["updated_at"] = current_time

    return result


def prepare_id(data: Dict[str, Any], id_field: str = "id") -> Dict[str, Any]:
    """
    Add UUID to data dictionary if ID field is not present.

    Args:
        data: Data dictionary to modify
        id_field: Name of the ID field

    Returns:
        Modified data dictionary

    Example:
        user_data = prepare_id({"name": "John"})
        # Adds id field with generated UUID
    """
    result = dict(data)

    if id_field not in result:
        result[id_field] = generate_uuid()

    return result
