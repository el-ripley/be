"""Result formatting utilities for SQL Query Tool."""

import uuid
import time
from typing import Any, Dict, List

import asyncpg

from src.api.openai_conversations.schemas import MessageResponse


def format_rows(rows: List[asyncpg.Record]) -> tuple[List[Dict[str, Any]], List[str]]:
    """
    Convert asyncpg rows to list of dicts with column names.

    Args:
        rows: List of asyncpg.Record objects

    Returns:
        Tuple of (result_rows, columns) where:
        - result_rows: List of dicts with row data
        - columns: List of column names
    """
    result_rows: List[Dict[str, Any]] = []
    columns: List[str] = []

    if rows:
        # Get column names from first row
        columns = list(rows[0].keys())

        # Convert each row to dict
        for row in rows:
            row_dict = {}
            for key in columns:
                value = row[key]
                # Convert asyncpg types to Python native types
                if value is None:
                    row_dict[key] = None
                elif isinstance(value, (int, float, str, bool)):
                    row_dict[key] = value
                else:
                    # For complex types, convert to string
                    row_dict[key] = str(value)
            result_rows.append(row_dict)

    return result_rows, columns


def parse_affected_rows(result: str) -> int:
    """
    Parse affected rows count from asyncpg execute result string.

    Args:
        result: Result string from asyncpg.execute() (e.g., "INSERT 0 1")

    Returns:
        Number of affected rows
    """
    affected_rows = 0
    if result:
        parts = result.split()
        if len(parts) >= 2:
            try:
                affected_rows = int(parts[-1])
            except (ValueError, IndexError):
                pass
    return affected_rows


def create_function_call_output(
    conv_id: str,
    call_id: str,
    function_output: Any,
) -> MessageResponse:
    """Create a function_call_output message."""
    output_uuid = str(uuid.uuid4())
    current_time = int(time.time() * 1000)

    return MessageResponse(
        id=output_uuid,
        conversation_id=conv_id,
        sequence_number=0,
        type="function_call_output",
        role="tool",
        content=None,
        call_id=call_id,
        function_output=function_output,
        status="completed",
        metadata=None,
        created_at=current_time,
        updated_at=current_time,
    )
