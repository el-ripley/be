"""SQL Query tool - allows agent to execute raw SQL queries with RLS protection.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):
   {
     "success": true,
     "row_count": int,
     "rows": [
       {
         "column1": value1,
         "column2": value2,
         ...
       },
       ...
     ],
     "columns": ["column1", "column2", ...]
   }

   OR on error:
   {
     "success": false,
     "error": "error message",
     "error_type": "PostgresError" | "ValueError" | etc.
   }
"""

from typing import Any, Dict, Optional

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.agent.tools.sql_query.error_handler import enhance_postgres_error
from src.agent.tools.sql_query.executors import (
    execute_read_query,
    execute_write_queries,
)
from src.agent.tools.sql_query.formatters import create_function_call_output
from src.agent.tools.sql_query.tool_description import TOOL_DESCRIPTION
from src.database.postgres.connection import (
    get_agent_reader_connection,
    get_agent_writer_transaction,
    get_suggest_response_reader_connection,
    get_suggest_response_writer_transaction,
)
from src.utils.logger import get_logger

logger = get_logger()


class SqlQueryTool(BaseTool):
    """Tool to execute raw SQL queries with RLS protection."""

    @property
    def name(self) -> str:
        return "sql_query"

    @property
    def definition(self) -> Dict[str, Any]:
        base_def = {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["read", "write"],
                        "description": "read: SELECT queries only. write: INSERT/UPDATE/DELETE queries in transaction.",
                    },
                    "sqls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "SQL statements array. READ mode: exactly 1 SELECT statement. WRITE mode: 1+ statements executed in transaction.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of what this query does and why (for logging/audit)",
                    },
                },
                "required": ["mode", "sqls", "description"],
                "additionalProperties": False,
            },
            "strict": True,
        }
        return self._apply_description_override(base_def)

    def _validate_arguments(self, arguments: Dict[str, Any]) -> Dict[str, Any] | None:
        """
        Validate tool arguments.

        Returns:
            Error dict if validation fails, None if valid
        """
        mode = arguments.get("mode")
        sqls = arguments.get("sqls")
        description = arguments.get("description")

        if not mode:
            return {
                "success": False,
                "error": "mode parameter is required",
                "error_type": "ValueError",
            }

        if mode not in ["read", "write"]:
            return {
                "success": False,
                "error": f"mode must be 'read' or 'write', got '{mode}'",
                "error_type": "ValueError",
            }

        if not sqls:
            return {
                "success": False,
                "error": "sqls parameter is required",
                "error_type": "ValueError",
            }

        if not isinstance(sqls, list):
            return {
                "success": False,
                "error": "sqls must be an array",
                "error_type": "ValueError",
            }

        if not description:
            return {
                "success": False,
                "error": "description parameter is required",
                "error_type": "ValueError",
            }

        # Validate sqls array
        if len(sqls) == 0:
            return {
                "success": False,
                "error": "sqls array cannot be empty",
                "error_type": "ValueError",
            }

        # Validate read mode: exactly 1 statement
        if mode == "read":
            if len(sqls) != 1:
                return {
                    "success": False,
                    "error": "READ mode requires exactly 1 SQL statement",
                    "error_type": "ValueError",
                }

        return None

    async def execute(
        self,
        conn: Optional[asyncpg.Connection],
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the SQL query. Uses passed conn for general_agent; creates suggest_response connection when in suggest_response context."""
        # Validate arguments
        validation_error = self._validate_arguments(arguments)
        if validation_error:
            return validation_error

        mode = arguments["mode"]
        sqls = arguments["sqls"]
        description = arguments["description"]

        # Strip whitespace from all statements
        sqls_trimmed = [sql.strip() for sql in sqls if sql.strip()]

        if len(sqls_trimmed) == 0:
            return {
                "success": False,
                "error": "All SQL statements are empty",
                "error_type": "ValueError",
            }

        # Detect: running in suggest_response context? (Facebook conversation RLS)
        is_suggest_response = (
            context.fb_conversation_type is not None
            and context.fb_conversation_id is not None
            and context.fan_page_id is not None
        )

        try:
            if is_suggest_response:
                # Create suggest_response connection với RLS (fb_conversation_type, fb_conversation_id)
                if mode == "read":
                    async with get_suggest_response_reader_connection(
                        user_id=context.user_id,
                        conversation_type=context.fb_conversation_type,
                        conversation_id=context.fb_conversation_id,
                        fan_page_id=context.fan_page_id,
                        page_scope_user_id=context.page_scope_user_id,
                    ) as sr_conn:
                        return await execute_read_query(
                            sr_conn, sqls_trimmed[0], description
                        )
                else:
                    async with get_suggest_response_writer_transaction(
                        user_id=context.user_id,
                        conversation_type=context.fb_conversation_type,
                        conversation_id=context.fb_conversation_id,
                        fan_page_id=context.fan_page_id,
                        page_scope_user_id=context.page_scope_user_id,
                    ) as sr_conn:
                        return await execute_write_queries(
                            sr_conn, sqls_trimmed, description
                        )
            else:
                # General agent: luôn tự mở connection/transaction (không dùng conn truyền vào)
                if mode == "read":
                    async with get_agent_reader_connection(
                        context.user_id
                    ) as agent_conn:
                        return await execute_read_query(
                            agent_conn, sqls_trimmed[0], description
                        )
                else:
                    async with get_agent_writer_transaction(
                        context.user_id
                    ) as agent_conn:
                        return await execute_write_queries(
                            agent_conn, sqls_trimmed, description
                        )

        except asyncpg.PostgresError as e:
            logger.error(f"SQL query error: {e}")
            # Use first SQL for error context
            enhanced_error = enhance_postgres_error(
                e, sqls_trimmed[0] if sqls_trimmed else ""
            )
            return {
                "success": False,
                "error": enhanced_error,
                "error_type": "PostgresError",
                "postgres_error_code": getattr(e, "sqlstate", None),
                "original_error": str(e),  # Include original for debugging
            }
        except Exception as e:
            logger.error(f"Unexpected error executing SQL query: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Process raw result into ToolResult."""

        # Extract metadata for tracking
        metadata = None
        if isinstance(raw_result, dict):
            metadata = {
                "description": raw_result.get("description", ""),
            }
            if "row_count" in raw_result:
                metadata["row_count"] = raw_result["row_count"]
            if "results" in raw_result:
                # For write mode, count total affected rows
                total_affected = sum(
                    r.get("affected", 0) for r in raw_result.get("results", [])
                )
                metadata["total_affected"] = total_affected
                metadata["statement_count"] = len(raw_result.get("results", []))

        output_message = create_function_call_output(
            conv_id=context.conv_id,
            call_id=context.call_id,
            function_output=(
                raw_result
                if isinstance(raw_result, dict)
                else {
                    "success": False,
                    "error": str(raw_result),
                    "error_type": "Unknown",
                }
            ),
        )

        return ToolResult(
            output_message=output_message,
            human_message=None,
            metadata=metadata,
        )
