"""
Async query execution utilities for high-performance SQL operations.

This module provides async parameter-safe query execution functions with
proper error handling and result formatting using asyncpg.
"""

from typing import List, Dict, Any, Optional
import asyncpg
import json
from src.utils.logger import get_logger

logger = get_logger()


def _parse_jsonb_fields(data: Dict[str, Any]) -> None:
    """
    Parse JSONB fields that asyncpg might return as strings.
    Common JSONB fields: participant_scope_users, settings, tasks, user_info, etc.
    """
    jsonb_fields = [
        "participant_scope_users",
        "settings",
        "tasks",
        "user_info",
        "metadata",
        "output",
        "input",
        "tools",
    ]
    for field in jsonb_fields:
        if field in data and isinstance(data[field], str):
            try:
                parsed = json.loads(data[field])
                data[field] = parsed
            except (json.JSONDecodeError, TypeError):
                # If parsing fails, leave as string
                pass


async def execute_async_query(
    conn: asyncpg.Connection, query: str, *args
) -> List[Dict[str, Any]]:
    """
    Execute an async SELECT query and return all results as dictionaries.

    Args:
        conn: Async database connection
        query: SQL query string with positional parameters ($1, $2, etc.)
        *args: Query parameters

    Returns:
        List of dictionaries representing rows

    Example:
        users = await execute_async_query(
            conn,
            "SELECT * FROM users WHERE created_at > $1",
            min_date
        )
    """
    try:
        results = await conn.fetch(query, *args)

        # Convert asyncpg Records to dictionaries and parse JSONB fields
        parsed_results = []
        for row in results:
            row_dict = dict(row)
            # Parse JSONB fields that might be returned as strings
            _parse_jsonb_fields(row_dict)
            parsed_results.append(row_dict)

        return parsed_results

    except asyncpg.PostgresError as e:
        logger.error(f"Async query execution error: {e}")
        logger.error(f"Query: {query}")
        logger.error(f"Args: {args}")
        raise
    except Exception as e:
        logger.error(f"Unexpected async query error: {e}")
        raise


async def execute_async_single(
    conn: asyncpg.Connection, query: str, *args
) -> Optional[Dict[str, Any]]:
    """
    Execute an async query and return a single result as dictionary.

    Args:
        conn: Async database connection
        query: SQL query string with positional parameters
        *args: Query parameters

    Returns:
        Dictionary representing the row, or None if no results

    Example:
        user = await execute_async_single(
            conn,
            "SELECT * FROM users WHERE id = $1",
            user_id
        )
    """
    try:
        result = await conn.fetchrow(query, *args)

        if result:
            try:
                result_dict = dict(result)
                # Parse JSONB fields that might be returned as strings
                _parse_jsonb_fields(result_dict)

                return result_dict
            except Exception as e:
                logger.error(f"Error converting Record to dict: {e}")
                logger.error(f"Record type: {type(result)}")
                logger.error(
                    f"Record keys: {list(result.keys()) if hasattr(result, 'keys') else 'N/A'}"
                )
                raise
        return None

    except asyncpg.PostgresError as e:
        logger.error(f"Async single query execution error: {e}")
        logger.error(f"Query: {query}")
        logger.error(f"Args: {args}")
        raise
    except Exception as e:
        logger.error(f"Unexpected async single query error: {e}")
        raise


async def execute_async_command(conn: asyncpg.Connection, query: str, *args) -> str:
    """
    Execute an async INSERT, UPDATE, or DELETE command.

    Args:
        conn: Async database connection
        query: SQL command string with positional parameters
        *args: Query parameters

    Returns:
        Command status string (e.g., 'INSERT 0 1', 'UPDATE 2', 'DELETE 1')

    Example:
        status = await execute_async_command(
            conn,
            "UPDATE users SET updated_at = $1 WHERE id = $2",
            timestamp, user_id
        )
    """
    try:
        status = await conn.execute(query, *args)

        logger.debug(f"Async command executed successfully: {status}")
        return status

    except asyncpg.PostgresError as e:
        logger.error(f"Async command execution error: {e}")
        logger.error(f"Query: {query}")
        logger.error(f"Args: {args}")
        raise
    except Exception as e:
        logger.error(f"Unexpected async command error: {e}")
        raise


async def execute_async_many(
    conn: asyncpg.Connection, query: str, args_list: List[tuple]
) -> None:
    """
    Execute the same async query with multiple parameter sets (batch operation).

    Args:
        conn: Async database connection
        query: SQL command string with positional parameters
        args_list: List of parameter tuples

    Example:
        # Batch insert users
        user_data = [
            ("123...", "John", 1609459200),
            ("456...", "Jane", 1609459260),
        ]
        await execute_async_many(
            conn,
            "INSERT INTO users (id, name, created_at) VALUES ($1, $2, $3)",
            user_data
        )
    """
    if not args_list:
        return

    try:
        await conn.executemany(query, args_list)

        logger.debug(
            f"Async batch command executed successfully, {len(args_list)} operations"
        )

    except asyncpg.PostgresError as e:
        logger.error(f"Async batch execution error: {e}")
        logger.error(f"Query: {query}")
        logger.error(f"Batch size: {len(args_list)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected async batch error: {e}")
        raise


async def execute_async_returning(
    conn: asyncpg.Connection, query: str, *args
) -> Optional[Dict[str, Any]]:
    """
    Execute an async INSERT/UPDATE command with RETURNING clause.

    Args:
        conn: Async database connection
        query: SQL command with RETURNING clause
        *args: Query parameters

    Returns:
        Dictionary with returned values, or None

    Example:
        result = await execute_async_returning(
            conn,
            '''INSERT INTO users (id, name, created_at)
               VALUES ($1, $2, $3)
               RETURNING id, created_at''',
            user_id, name, created_at
        )
        # result = {"id": "123...", "created_at": 1609459200}
    """
    try:
        result = await conn.fetchrow(query, *args)

        logger.debug("Async returning command executed successfully")
        result_dict = dict(result) if result else None

        return result_dict

    except asyncpg.PostgresError as e:
        logger.error(f"Async returning command execution error: {e}")
        logger.error(f"Query: {query}")
        logger.error(f"Args: {args}")
        raise
    except Exception as e:
        logger.error(f"Unexpected async returning error: {e}")
        raise


async def execute_async_scalar(conn: asyncpg.Connection, query: str, *args) -> Any:
    """
    Execute an async query and return a single scalar value.

    Args:
        conn: Async database connection
        query: SQL query string that returns a single value
        *args: Query parameters

    Returns:
        The scalar value from the query result

    Example:
        count = await execute_async_scalar(
            conn,
            "SELECT COUNT(*) FROM users WHERE created_at > $1",
            min_date
        )
    """
    try:
        result = await conn.fetchval(query, *args)

        return result

    except asyncpg.PostgresError as e:
        logger.error(f"Async scalar query execution error: {e}")
        logger.error(f"Query: {query}")
        logger.error(f"Args: {args}")
        raise
    except Exception as e:
        logger.error(f"Unexpected async scalar error: {e}")
        raise


# High-performance batch operations
async def bulk_insert_async(
    conn: asyncpg.Connection, table_name: str, columns: List[str], data: List[List[Any]]
) -> None:
    """
    High-performance bulk insert using asyncpg's copy protocol.

    Args:
        conn: Async database connection
        table_name: Target table name
        columns: List of column names
        data: List of rows (each row is a list of values)

    Example:
        await bulk_insert_async(
            conn,
            "users",
            ["id", "name", "created_at"],
            [
                ["123...", "John", 1609459200],
                ["456...", "Jane", 1609459260],
            ]
        )
    """
    try:
        # Use COPY for maximum performance
        await conn.copy_records_to_table(table_name, records=data, columns=columns)

        logger.debug(f"Bulk insert completed: {len(data)} records to {table_name}")

    except asyncpg.PostgresError as e:
        logger.error(f"Bulk insert error: {e}")
        logger.error(f"Table: {table_name}, Columns: {columns}")
        raise
    except Exception as e:
        logger.error(f"Unexpected bulk insert error: {e}")
        raise


# Query building helpers for async (since asyncpg uses $1, $2 instead of named params)
def build_async_insert_query(table: str, columns: List[str]) -> str:
    """
    Build a parameterized INSERT query for asyncpg ($1, $2, etc.).

    Args:
        table: Table name
        columns: List of column names

    Returns:
        SQL query string with positional parameters

    Example:
        query = build_async_insert_query("users", ["id", "name", "created_at"])
        # Returns: "INSERT INTO users (id, name, created_at) VALUES ($1, $2, $3)"
    """
    placeholders = [f"${i+1}" for i in range(len(columns))]
    columns_str = ", ".join(columns)
    placeholders_str = ", ".join(placeholders)

    return f"INSERT INTO {table} ({columns_str}) VALUES ({placeholders_str})"


def build_async_update_query(
    table: str, columns: List[str], where_column: str = "id"
) -> str:
    """
    Build a parameterized UPDATE query for asyncpg.

    Args:
        table: Table name
        columns: List of column names to update
        where_column: Column name for WHERE clause

    Returns:
        SQL query string with positional parameters

    Example:
        query = build_async_update_query("users", ["name", "updated_at"])
        # Returns: "UPDATE users SET name = $1, updated_at = $2 WHERE id = $3"
    """
    set_clauses = [f"{col} = ${i+1}" for i, col in enumerate(columns)]
    where_param = f"${len(columns) + 1}"

    return f"UPDATE {table} SET {', '.join(set_clauses)} WHERE {where_column} = {where_param}"


# Performance monitoring
class AsyncQueryStats:
    """Track async query performance statistics."""

    def __init__(self):
        self.query_times = []
        self.slow_query_threshold = 1.0  # seconds

    async def execute_with_timing(
        self, executor_func, conn: asyncpg.Connection, query: str, *args
    ):
        """Execute query and track timing."""
        import time

        start_time = time.time()
        try:
            result = await executor_func(conn, query, *args)
            execution_time = time.time() - start_time

            self.query_times.append(execution_time)

            if execution_time > self.slow_query_threshold:
                logger.warning(
                    f"Slow query detected: {execution_time:.2f}s - {query[:100]}..."
                )

            return result

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"Query failed after {execution_time:.2f}s: {e}")
            raise

    def get_stats(self) -> dict:
        """Get query performance statistics."""
        if not self.query_times:
            return {"message": "No queries executed yet"}

        import statistics

        return {
            "total_queries": len(self.query_times),
            "avg_time": statistics.mean(self.query_times),
            "median_time": statistics.median(self.query_times),
            "max_time": max(self.query_times),
            "slow_queries": sum(
                1 for t in self.query_times if t > self.slow_query_threshold
            ),
        }
