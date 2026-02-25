"""Query execution logic for SQL Query Tool.

Caller (sql_query tool) is responsible for obtaining and passing the connection.
Executors never create connections or transactions.
"""

from typing import Any, Dict, List

import asyncpg

from src.agent.tools.sql_query.formatters import format_rows, parse_affected_rows


async def execute_read_query(
    conn: asyncpg.Connection, sql: str, description: str
) -> Dict[str, Any]:
    """
    Execute a SELECT query in read mode.

    Args:
        conn: Database connection (caller must set RLS context if needed).
        sql: SQL SELECT statement
        description: Query description

    Returns:
        Result dict with success, row_count, rows, columns, description
    """
    rows = await conn.fetch(sql)
    result_rows, columns = format_rows(rows)
    return {
        "success": True,
        "row_count": len(result_rows),
        "rows": result_rows,
        "columns": columns,
        "description": description,
    }


async def execute_write_queries(
    conn: asyncpg.Connection, sqls: List[str], description: str
) -> Dict[str, Any]:
    """
    Execute INSERT/UPDATE/DELETE queries in write mode (transaction).

    Caller must pass a connection that is already inside a transaction.

    Args:
        conn: Database connection in transaction (caller must manage transaction).
        sqls: List of SQL statements to execute in transaction
        description: Query description

    Returns:
        Result dict with success, results array, description
    """
    results = []
    for sql in sqls:
        sql_upper = sql.upper()
        has_returning = "RETURNING" in sql_upper
        if has_returning:
            rows = await conn.fetch(sql)
            result_rows, columns = format_rows(rows)
            results.append(
                {
                    "affected": len(result_rows),
                    "rows": result_rows,
                    "columns": columns,
                }
            )
        else:
            result = await conn.execute(sql)
            results.append({"affected": parse_affected_rows(result)})
    return {"success": True, "results": results, "description": description}
