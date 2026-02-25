"""Error handling utilities for SQL Query Tool."""

import asyncpg


def enhance_postgres_error(error: asyncpg.PostgresError, sql: str) -> str:
    """
    Enhance Postgres error messages with helpful context for the agent.

    Args:
        error: The PostgresError exception
        sql: The SQL query that caused the error

    Returns:
        Enhanced error message string
    """
    error_msg = str(error)
    error_code = getattr(error, "sqlstate", None)

    # Common error patterns and helpful messages
    # UUID type mismatch: string literal needs cast
    if "is of type uuid but expression is of type text" in error_msg.lower():
        import re

        # Extract column name from error message
        match = re.search(r'column "([^"]+)"', error_msg)
        column_name = match.group(1) if match else "the column"
        return (
            f"UUID cast required: column '{column_name}' expects UUID type. "
            f"Cast string literals with ::uuid, e.g. 'your-uuid-value'::uuid. "
            f"Example: INSERT INTO table ({column_name}) VALUES ('abc-123'::uuid)"
        )

    if "invalid input syntax for type uuid" in error_msg.lower():
        # Extract the problematic value if possible
        import re

        match = re.search(r"uuid: \"([^\"]+)\"", error_msg)
        if match:
            bad_value = match.group(1)
            return (
                f"Type mismatch: Value '{bad_value}' is not a valid UUID. "
                f"This looks like a Facebook ID (format: numbers with underscores). "
                f"Check your query - you may be using a Facebook ID where a UUID is expected, "
                f"or vice versa. Facebook IDs are VARCHAR, not UUID."
            )
        return (
            "Type mismatch: Invalid UUID format. "
            "Facebook IDs (like post_id, comment_id, page_id) are VARCHAR strings, not UUIDs. "
            "Only internal database IDs (like suggest_response_* table IDs) are UUIDs."
        )

    if "invalid input syntax for type integer" in error_msg.lower():
        return (
            "Type mismatch: Invalid integer format. "
            "Check that numeric values in your query are actual numbers, not strings with quotes."
        )

    if "column" in error_msg.lower() and "does not exist" in error_msg.lower():
        return (
            f"Column not found: {error_msg}. "
            "Check the schema documentation in the tool description for available columns."
        )

    if "relation" in error_msg.lower() and "does not exist" in error_msg.lower():
        return (
            f"Table not found: {error_msg}. "
            "Check the schema documentation in the tool description for available tables."
        )

    # Unique constraint violation (error_code 23505)
    if error_code == "23505":
        if "uq_active_page_scope_user_memory" in error_msg:
            return (
                f"Duplicate active container: {error_msg}. "
                "An active page_scope_user_memory container already exists for this customer. "
                "Do NOT create a new one. Instead, use a READ query to fetch the existing container's id: "
                "SELECT id FROM page_scope_user_memory WHERE is_active = TRUE LIMIT 1; "
                "Then INSERT blocks using that id as prompt_id."
            )
        return (
            f"Unique constraint violation: {error_msg}. "
            "A record with these values already exists. Check existing data before inserting."
        )

    # ON CONFLICT with no matching constraint
    if "no unique or exclusion constraint matching the on conflict" in error_msg.lower():
        return (
            f"ON CONFLICT not supported on this table: {error_msg}. "
            "The memory_blocks table has NO unique constraints — it is append-only. "
            "Do NOT use INSERT ... ON CONFLICT. "
            "To add a block: use plain INSERT INTO memory_blocks (...) VALUES (...). "
            "To update an existing block: use UPDATE memory_blocks SET content = '...' WHERE id = 'block-uuid'::uuid."
        )

    if error_code == "23503":  # Foreign key violation
        return (
            f"Foreign key constraint violation: {error_msg}. "
            "The referenced record does not exist or you don't have access to it (RLS filtered)."
        )

    if error_code == "42P01":  # Undefined table
        return (
            f"Table does not exist: {error_msg}. "
            "Check the schema documentation in the tool description for available tables."
        )

    if error_code == "42703":  # Undefined column
        return (
            f"Column does not exist: {error_msg}. "
            "Check the schema documentation in the tool description for available columns."
        )

    # Return original error if no enhancement available
    return error_msg
