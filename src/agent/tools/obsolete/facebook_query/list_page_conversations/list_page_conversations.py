"""List page conversations tool - list inbox conversations for a page.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):
   {
     "success": true,
     "page_id": str,
     "conversations": [
       {
         "conversation_id": str,  # Use with get_inbox_or_comment_thread
         "user_name": str,
         "user_avatar": str | None,
         "latest_message_preview": str | None,  # First 200 chars
         "latest_message_time": int | None,  # timestamp
         "is_unread": bool
       }
       // ... paginated results
     ],
     "total_count": int,
     "limit": int,
     "offset": int,
     "has_more": bool
   }
"""

import json
import time
import uuid
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.services.facebook.messages.message_read_service import MessageReadService
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
List inbox conversations (Messenger) for a Facebook page.

WHEN TO USE:
- View recent or unread inbox messages
- Find conversations with specific users
- Before reading full conversation with get_inbox_or_comment_thread

PREREQUISITES:
- Requires page_id (which you should already have from system prompt)

NEXT STEPS:
- Use get_inbox_or_comment_thread(item_id, item_type="fb_conv_messages") for full messages

RETURNS: Array of conversations with id, user name, latest message preview, unread status.
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


class ListPageConversationsTool(BaseTool):
    """Tool to list inbox conversations for a Facebook page."""

    def __init__(self, read_service: MessageReadService = None):
        self._read_service = read_service or MessageReadService()

    @property
    def name(self) -> str:
        return "list_page_conversations"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Facebook page ID",
                    },
                    "filter": {
                        "type": "string",
                        "enum": ["all", "unread"],
                        "default": "all",
                        "description": "Filter conversations: 'all' for all conversations, 'unread' for unread only",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                        "description": "Maximum number of conversations to return.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "Offset for pagination.",
                    },
                },
                "required": ["page_id", "filter", "limit", "offset"],
                "additionalProperties": False,
            },
            "strict": True,
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - list page conversations."""
        page_id = arguments.get("page_id")
        if not page_id:
            return {"success": False, "error": "page_id is required"}

        filter_type = arguments.get("filter", "all")
        limit = arguments.get("limit", 20)
        offset = arguments.get("offset", 0)

        (
            conversations,
            total_count,
            has_more,
        ) = await self._read_service.list_inbox_conversations(
            conn,
            page_id,
            limit=limit,
            offset=offset,
            filter_type=filter_type,
        )

        # Format conversations for output
        formatted_conversations = []
        for conv in conversations:
            user_info_raw = conv.get("user_info") or {}
            if isinstance(user_info_raw, str):
                try:
                    user_info = json.loads(user_info_raw)
                except (json.JSONDecodeError, TypeError):
                    user_info = {}
            else:
                user_info = user_info_raw or {}

            latest_msg = None
            if conv.get("latest_message_text"):
                latest_msg = conv.get("latest_message_text", "")[:200]

            formatted_conversations.append(
                {
                    "conversation_id": conv.get("conversation_id"),
                    "user_name": user_info.get("name") or "Unknown User",
                    "user_avatar": user_info.get("profile_pic"),
                    "latest_message_preview": latest_msg,
                    "latest_message_time": conv.get("latest_message_facebook_timestamp")
                    or conv.get("latest_message_created_at"),
                    "is_unread": conv.get("unread_count", 0) > 0
                    or not conv.get("mark_as_read", False),
                }
            )

        return {
            "success": True,
            "page_id": page_id,
            "conversations": formatted_conversations,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
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
