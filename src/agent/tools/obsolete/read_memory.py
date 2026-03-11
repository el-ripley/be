"""Read memory tool - read a specific memory with full data structure."""

import time
import uuid
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.services.suggest_response.memory_blocks_service import MemoryBlocksService
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
Read a specific memory (page_memory or user_memory) with full data structure.

Returns the memory container plus all blocks with their media attachments.
Use this to understand current memory state before making edits.

RETURNS:
{
  "memory": {
    "id": "uuid",
    "fan_page_id": "...",
    "prompt_type": "messages" | "comments",  // for page_memory
    "psid": "...",  // for user_memory
    "is_active": true,
    "created_at": 1234567890
  },
  "blocks": [
    {
      "block_key": "intro",
      "title": "Gioi thieu Shop",
      "content": "...",
      "display_order": 1,
      "media": [...]
    },
    ...
  ],
  "rendered_text": "<full rendered memory as it appears in prompt>"
}
"""


def _create_function_call_output(
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


class ReadMemoryTool(BaseTool):
    """Tool to read a specific memory with full data structure."""

    def __init__(self, memory_service: MemoryBlocksService = None):
        self._memory_service = memory_service or MemoryBlocksService()

    @property
    def name(self) -> str:
        return "read_memory"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {
                        "type": "string",
                        "enum": ["page_memory", "user_memory"],
                        "description": "Which memory type to read",
                    },
                    "fan_page_id": {
                        "type": "string",
                        "description": "Facebook page ID",
                    },
                    "prompt_type": {
                        "type": "string",
                        "enum": ["messages", "comments"],
                        "description": "Required for page_memory",
                    },
                    "psid": {
                        "type": "string",
                        "description": "Required for user_memory",
                    },
                },
                "required": ["memory_type", "fan_page_id"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - read memory."""
        memory_type = arguments.get("memory_type")
        fan_page_id = arguments.get("fan_page_id")
        prompt_type = arguments.get("prompt_type")
        psid = arguments.get("psid")

        if not memory_type or not fan_page_id:
            return {
                "success": False,
                "error": "memory_type and fan_page_id are required",
            }

        if memory_type == "page_memory" and not prompt_type:
            return {
                "success": False,
                "error": "prompt_type is required for page_memory",
            }

        if memory_type == "user_memory" and not psid:
            return {
                "success": False,
                "error": "psid is required for user_memory",
            }

        try:
            # Get or create prompt container
            container = await self._memory_service.get_or_create_prompt_container(
                memory_type=memory_type,
                fan_page_id=fan_page_id,
                prompt_type=prompt_type,
                psid=psid,
                owner_user_id=context.user_id,
                created_by_type="agent",
            )

            prompt_id = container["prompt_id"]
            prompt_type_for_blocks = container["prompt_type_for_blocks"]

            # Get blocks
            blocks = await self._memory_service.list_blocks(
                prompt_type_for_blocks, prompt_id
            )

            # Get memory container info
            if memory_type == "page_memory":
                from src.database.postgres.repositories.suggest_response_queries import (
                    get_active_page_prompt,
                )

                memory_record = await get_active_page_prompt(
                    conn, fan_page_id, prompt_type, context.user_id
                )
                memory_info = {
                    "id": str(memory_record["id"]) if memory_record else None,
                    "fan_page_id": fan_page_id,
                    "prompt_type": prompt_type,
                    "is_active": memory_record["is_active"] if memory_record else True,
                    "created_at": memory_record["created_at"]
                    if memory_record
                    else None,
                }
            else:  # user_memory
                from src.database.postgres.repositories.suggest_response_queries import (
                    get_active_page_scope_user_prompt,
                )

                memory_record = await get_active_page_scope_user_prompt(
                    conn, fan_page_id, psid, context.user_id
                )
                memory_info = {
                    "id": str(memory_record["id"]) if memory_record else None,
                    "fan_page_id": fan_page_id,
                    "psid": psid,
                    "is_active": memory_record["is_active"] if memory_record else True,
                    "created_at": memory_record["created_at"]
                    if memory_record
                    else None,
                }

            # Render memory text
            rendered_text = await self._memory_service.render_memory(
                prompt_type_for_blocks, prompt_id
            )

            return {
                "success": True,
                "memory": memory_info,
                "blocks": blocks,
                "rendered_text": rendered_text,
            }

        except Exception as e:
            logger.error(f"Error in read_memory: {str(e)}")
            return {"success": False, "error": f"Internal error: {str(e)}"}

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Process raw result into ToolResult."""
        output_message = _create_function_call_output(
            conv_id=context.conv_id,
            call_id=context.call_id,
            function_output=(
                raw_result
                if isinstance(raw_result, dict)
                else {"error": str(raw_result)}
            ),
        )

        return ToolResult(
            output_message=output_message,
            human_message=None,
            metadata=None,
        )
