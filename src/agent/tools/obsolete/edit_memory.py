"""Edit memory tool - all write operations on memory blocks and prompt containers."""

import uuid
import time
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.services.suggest_response.memory_blocks_service import MemoryBlocksService
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
Edit memory blocks for page_memory or user_memory.

ACTIONS:

Block-level operations:
- add_block: Add a new block (fails if block_key exists)
- update_block: Update existing block content/title/order
- remove_block: Remove a block by block_key
- reorder_blocks: Change display order of multiple blocks

Prompt container operations (for clean track record):
- create_fresh_prompt: Create new empty prompt container, deactivate old one
- migrate_prompt: Create new prompt container and copy all blocks from old one

WHEN TO USE CONTAINER OPERATIONS:
- When prompt has been edited too many times and track_record is messy
- When you want a "clean slate" for new tracking
- create_fresh_prompt: Start completely fresh (no blocks copied)
- migrate_prompt: Keep current blocks but reset track history
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


class EditMemoryTool(BaseTool):
    """Tool for all write operations on memory blocks."""

    def __init__(self, memory_service: MemoryBlocksService = None):
        self._memory_service = memory_service or MemoryBlocksService()

    @property
    def name(self) -> str:
        return "edit_memory"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "add_block",
                            "update_block",
                            "remove_block",
                            "reorder_blocks",
                            "create_fresh_prompt",
                            "migrate_prompt",
                        ],
                    },
                    "memory_type": {
                        "type": "string",
                        "enum": ["page_memory", "user_memory"],
                    },
                    "fan_page_id": {"type": "string"},
                    "prompt_type": {
                        "type": "string",
                        "enum": ["messages", "comments"],
                        "description": "Required for page_memory",
                    },
                    "psid": {
                        "type": "string",
                        "description": "Required for user_memory",
                    },
                    "block_key": {
                        "type": "string",
                        "description": "Stable identifier for the block",
                    },
                    "title": {
                        "type": "string",
                        "description": "Human-readable title shown in rendered prompt",
                    },
                    "content": {
                        "type": "string",
                        "description": "Block content text",
                    },
                    "display_order": {
                        "type": "integer",
                        "description": "Position in rendered prompt (lower = first)",
                    },
                    "media": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "media_id": {"type": "string"},
                                "display_order": {"type": "integer"},
                            },
                            "required": ["media_id", "display_order"],
                        },
                        "description": "Media attachments for this block",
                    },
                    "block_order": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of block_keys in desired order",
                    },
                },
                "required": ["action", "memory_type", "fan_page_id"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - edit memory."""
        action = arguments.get("action")
        memory_type = arguments.get("memory_type")
        fan_page_id = arguments.get("fan_page_id")
        prompt_type = arguments.get("prompt_type")
        psid = arguments.get("psid")

        if not action or not memory_type or not fan_page_id:
            return {
                "success": False,
                "error": "action, memory_type, and fan_page_id are required",
            }

        # Validate memory_type specific params
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
            # For container operations, execute directly
            if action == "create_fresh_prompt":
                result = await self._memory_service.create_fresh_prompt(
                    memory_type=memory_type,
                    fan_page_id=fan_page_id,
                    prompt_type=prompt_type,
                    psid=psid,
                    owner_user_id=context.user_id,
                    created_by_type="agent",
                )
                return {"success": True, "action": action, **result}

            if action == "migrate_prompt":
                result = await self._memory_service.migrate_prompt(
                    memory_type=memory_type,
                    fan_page_id=fan_page_id,
                    prompt_type=prompt_type,
                    psid=psid,
                    owner_user_id=context.user_id,
                    created_by_type="agent",
                )
                return {"success": True, "action": action, **result}

            # For block operations, get prompt container first
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

            if action == "add_block":
                block_key = arguments.get("block_key")
                title = arguments.get("title")
                content = arguments.get("content")
                display_order = arguments.get("display_order")
                media = arguments.get("media")

                if not block_key or not title or not content:
                    return {
                        "success": False,
                        "error": "block_key, title, and content are required for add_block",
                    }

                block = await self._memory_service.add_block(
                    memory_type=prompt_type_for_blocks,
                    prompt_id=prompt_id,
                    block_key=block_key,
                    title=title,
                    content=content,
                    display_order=display_order,
                    created_by_type="agent",
                    owner_user_id=context.user_id,
                    media=media,
                )
                return {"success": True, "action": action, "block": block}

            if action == "update_block":
                block_key = arguments.get("block_key")

                if not block_key:
                    return {
                        "success": False,
                        "error": "block_key is required for update_block",
                    }

                block = await self._memory_service.update_block(
                    memory_type=prompt_type_for_blocks,
                    prompt_id=prompt_id,
                    block_key=block_key,
                    title=arguments.get("title"),
                    content=arguments.get("content"),
                    display_order=arguments.get("display_order"),
                    created_by_type="agent",
                    owner_user_id=context.user_id,
                    media=arguments.get("media"),
                )
                return {"success": True, "action": action, "block": block}

            if action == "remove_block":
                block_key = arguments.get("block_key")

                if not block_key:
                    return {
                        "success": False,
                        "error": "block_key is required for remove_block",
                    }

                success = await self._memory_service.remove_block(
                    memory_type=prompt_type_for_blocks,
                    prompt_id=prompt_id,
                    block_key=block_key,
                    created_by_type="agent",
                )
                return {"success": success, "action": action, "block_key": block_key}

            if action == "reorder_blocks":
                block_order = arguments.get("block_order")

                if not block_order:
                    return {
                        "success": False,
                        "error": "block_order is required for reorder_blocks",
                    }

                blocks = await self._memory_service.reorder_blocks(
                    memory_type=prompt_type_for_blocks,
                    prompt_id=prompt_id,
                    block_order=block_order,
                    created_by_type="agent",
                )
                return {"success": True, "action": action, "blocks": blocks}

            return {"success": False, "error": f"Unknown action: {action}"}

        except ValueError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error in edit_memory: {str(e)}")
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
