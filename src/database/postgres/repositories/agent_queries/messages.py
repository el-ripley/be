"""
Message operations and JSON field decoding utilities.
"""

import json
from typing import Any, Dict, Optional, Sequence

import asyncpg

from src.database.postgres.executor import execute_async_single
from src.database.postgres.entities import OpenAIMessage


def _decode_json_fields(data: Dict[str, Any], fields: Sequence[str]) -> None:
    """Decode JSON string fields in-place."""
    for field in fields:
        value = data.get(field)
        if value is None or isinstance(value, (dict, list)):
            continue

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                data[field] = None
                continue
            try:
                data[field] = json.loads(stripped)
            except json.JSONDecodeError:
                # Leave as original string if it isn't valid JSON
                continue


async def get_message(
    conn: asyncpg.Connection,
    message_id: str,
) -> Optional[OpenAIMessage]:
    """Get message by ID."""
    query = "SELECT * FROM openai_message WHERE id = $1"
    row = await execute_async_single(conn, query, message_id)

    if not row:
        return None

    data = dict(row)
    for uuid_field in ("id", "conversation_id"):
        if data.get(uuid_field) is not None:
            data[uuid_field] = str(data[uuid_field])

    _decode_json_fields(
        data,
        (
            "content",
            "reasoning_summary",
            "function_arguments",
            "function_output",
            "metadata",
            "web_search_action",
            "annotations",
        ),
    )
    return OpenAIMessage(**data)
