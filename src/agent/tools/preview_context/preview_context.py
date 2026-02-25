"""Preview suggest response context tool - preview full context that suggest_response_agent receives."""

import uuid
import time
from typing import Any, Dict, List, Optional

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.services.suggest_response.memory_blocks_service import MemoryBlocksService
from src.agent.suggest_response.context.prompts.prompt_loader import (
    load_static_suggest_response_system_prompt,
)
from src.agent.suggest_response.context.context_builder import (
    SuggestResponseContextBuilder,
)
from src.database.postgres.repositories.facebook_queries.messages.conversations import (
    get_conversation_with_details,
)
from src.agent.tools.preview_context.example_data import (
    EXAMPLE_MESSAGES_CONVERSATION,
    EXAMPLE_COMMENTS_CONVERSATION,
)
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
Preview rendered memory (page_memory, user_memory) or full suggest_response_agent context. Does not generate suggestions.

Two modes:

1) Memory preview (no conversation_id): pass conversation_type + fan_page_id (+ psid for user_memory). Returns rendered memory blocks only. Use `include` to add system_prompt or conversation_data if needed.

2) Full context (with conversation_id): pass conversation_type + conversation_id. Builds the complete context identical to what suggest_response_agent receives. Use when diagnosing a specific conversation's behavior.

Output is visible to both you and the user.
"""


def _input_messages_to_preview_string(input_messages: List[Dict[str, Any]]) -> str:
    """Convert suggest_response input_messages to the same preview string format."""
    role_tag_map = {
        "system": "SystemMessage",
        "assistant": "AssistantMessage",
    }
    parts: List[str] = []
    for msg in input_messages:
        role = msg.get("role") or "user"
        raw_content = msg.get("content") or ""
        # Handle array-of-objects content
        if isinstance(raw_content, list):
            text_parts = [
                block.get("text", "")
                for block in raw_content
                if isinstance(block, dict) and block.get("text")
            ]
            content = "\n\n".join(text_parts).strip()
        else:
            content = raw_content.strip()
        if not content:
            continue
        tag = role_tag_map.get(role, "UserMessage")
        parts.append(f"<{tag}>\n{content}\n</{tag}>")
    return "\n\n".join(parts)


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


class PreviewSuggestResponseContextTool(BaseTool):
    """Tool to preview full suggest_response context."""

    def __init__(self, memory_service: MemoryBlocksService = None):
        self._memory_service = memory_service or MemoryBlocksService()

    @property
    def name(self) -> str:
        return "preview_suggest_response_context"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_type": {
                        "type": "string",
                        "enum": ["messages", "comments"],
                        "description": "messages or comments",
                    },
                    "conversation_id": {
                        "type": "string",
                        "description": "Real conversation mode: conversation UUID (fb_conversation_messages.id or facebook_conversation_comments.id). fan_page_id is auto-resolved.",
                    },
                    "fan_page_id": {
                        "type": "string",
                        "description": "Example mode only: Facebook page ID. Not needed when conversation_id is provided.",
                    },
                    "psid": {
                        "type": "string",
                        "description": "Example mode only (messages): PSID to include user_memory.",
                    },
                    "include": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "system_prompt",
                                "page_memory",
                                "user_memory",
                                "conversation_data",
                            ],
                        },
                        "description": "Example mode only: which items to include. Default: all.",
                    },
                },
                "required": ["conversation_type"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - preview context (real conversation or example mode)."""
        conversation_type = arguments.get("conversation_type")
        if not conversation_type:
            return {
                "success": False,
                "error": "conversation_type is required",
            }

        conversation_id: Optional[str] = arguments.get("conversation_id") or None
        fan_page_id: Optional[str] = arguments.get("fan_page_id") or None
        psid = arguments.get("psid") or None
        include = arguments.get("include")

        try:
            # Real conversation mode: conversation_id provided
            if conversation_id:
                return await self._execute_real_conversation(
                    conn=conn,
                    context=context,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                )

            # Example mode: fan_page_id required
            if not fan_page_id:
                return {
                    "success": False,
                    "error": "Either conversation_id (real mode) or fan_page_id (example mode) is required",
                }
            return await self._execute_example_mode(
                conn=conn,
                context=context,
                conversation_type=conversation_type,
                fan_page_id=fan_page_id,
                psid=psid,
                include=include,
            )
        except Exception as e:
            logger.error(f"Error in preview_suggest_response_context: {str(e)}")
            return {"success": False, "error": f"Internal error: {str(e)}"}

    async def _execute_real_conversation(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        conversation_type: str,
        conversation_id: str,
    ) -> Dict[str, Any]:
        """Build context for a real conversation the same way suggest_response_agent does.

        Resolves fan_page_id and PSID from the conversation record in DB.
        """
        fan_page_id: Optional[str] = None
        facebook_page_scope_user_id: Optional[str] = None

        if conversation_type == "messages":
            conv = await get_conversation_with_details(conn, conversation_id)
            if not conv:
                return {
                    "success": False,
                    "error": f"Conversation not found for messages: {conversation_id}",
                }
            fan_page_id = conv.get("fan_page_id")
            facebook_page_scope_user_id = conv.get("facebook_page_scope_user_id")

        elif conversation_type == "comments":
            from src.database.postgres.repositories.facebook_queries.comments.comment_conversations import (
                get_conversation_by_id as get_comment_conversation_by_id,
            )

            conv = await get_comment_conversation_by_id(conn, conversation_id)
            if not conv:
                return {
                    "success": False,
                    "error": f"Conversation not found for comments: {conversation_id}",
                }
            fan_page_id = conv.get("fan_page_id")

        if not fan_page_id:
            return {
                "success": False,
                "error": f"Could not resolve fan_page_id from conversation: {conversation_id}",
            }

        context_builder = SuggestResponseContextBuilder()
        input_messages, metadata = await context_builder.build_context(
            conn=conn,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            fan_page_id=fan_page_id,
            owner_user_id=context.user_id,
            facebook_page_scope_user_id=facebook_page_scope_user_id,
        )
        full_context = _input_messages_to_preview_string(input_messages)
        return {
            "success": True,
            "context_preview": full_context,
            "mode": "real_conversation",
            "conversation_id": conversation_id,
            "fan_page_id": fan_page_id,
            "metadata": metadata,
        }

    async def _execute_example_mode(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        conversation_type: str,
        fan_page_id: str,
        psid: Optional[str] = None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Preview with hardcoded example data (no conversation_id).

        Mirrors the real context structure:
        - Comments: SystemMessage = system_prompt + page_memory; UserMessages for conversation/escalation/runtime
        - Messages: SystemMessage = system_prompt + page_memory + user_memory; real user/assistant turns
        """
        if not include:
            include = ["page_memory"]
            if conversation_type == "messages" and psid:
                include.append("user_memory")

        parts = []

        # Build system message (instructions + memories merged together)
        system_parts = []
        if "system_prompt" in include:
            system_prompt = load_static_suggest_response_system_prompt(
                conversation_type
            )
            system_parts.append(system_prompt)

        if "page_memory" in include:
            container = await self._memory_service.get_or_create_prompt_container(
                memory_type="page_memory",
                fan_page_id=fan_page_id,
                prompt_type=conversation_type,
                owner_user_id=context.user_id,
                created_by_type="agent",
            )
            prompt_id = container["prompt_id"]
            prompt_type_for_blocks = container["prompt_type_for_blocks"]
            page_memory_text = await self._memory_service.render_memory(
                prompt_type_for_blocks, prompt_id
            )
            if page_memory_text.strip():
                system_parts.append(
                    f"<page_memory>\n{page_memory_text}\n</page_memory>"
                )

        if "user_memory" in include and conversation_type == "messages" and psid:
            container = await self._memory_service.get_or_create_prompt_container(
                memory_type="user_memory",
                fan_page_id=fan_page_id,
                psid=psid,
                owner_user_id=context.user_id,
                created_by_type="agent",
            )
            prompt_id = container["prompt_id"]
            prompt_type_for_blocks = container["prompt_type_for_blocks"]
            user_memory_text = await self._memory_service.render_memory(
                prompt_type_for_blocks, prompt_id
            )
            if user_memory_text.strip():
                system_parts.append(
                    f"<user_memory>\n{user_memory_text}\n</user_memory>"
                )

        if system_parts:
            system_content = "\n\n".join(system_parts)
            parts.append(f"<SystemMessage>\n{system_content}\n</SystemMessage>")

        if "conversation_data" in include:
            if conversation_type == "comments":
                # Comments: plain text in UserMessage with <conversation_data> wrapper
                conv_data = EXAMPLE_COMMENTS_CONVERSATION
                parts.append(
                    f"<UserMessage>\n<conversation_data>\n{conv_data}\n</conversation_data>\n</UserMessage>"
                )
            else:
                # Messages: example shown as user/assistant turns
                conv_data = EXAMPLE_MESSAGES_CONVERSATION
                parts.append(
                    f"<UserMessage>\n{conv_data}\n</UserMessage>"
                )

        full_context = "\n\n".join(parts)
        return {
            "success": True,
            "context_preview": full_context,
            "mode": "example",
            "items_included": include,
        }

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
