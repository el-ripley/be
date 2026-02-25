"""List post comment threads tool - list comment threads on a post.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):
   {
     "success": true,
     "post_id": str,
     "threads": [
       {
         "root_comment_id": str,  # Use with get_inbox_or_comment_thread
         "conversation_id": str,  # Facebook conversation comments ID
         "user_name": str,
         "message_preview": str | None,  # Currently None, would need root comment fetch
         "reply_count": int,  # Number of replies (excluding root comment)
         "has_page_reply": bool,  # True if page has replied to this thread
         "is_unread": bool,
         "like_count": int  # Currently 0, would need root comment fetch
       }
       // ... paginated results
     ],
     "total_count": int,
     "limit": int,
     "offset": int,
     "has_more": bool
   }
"""

import uuid
import time
import json
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.services.facebook.comments.comment_read_service import CommentReadService
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
List comment threads (root comments + replies) on a Facebook post.

WHEN TO USE:
- View all comment threads on a specific post
- Find threads needing page reply (no_page_reply filter)
- Find unread comment threads
- Before reading full thread with get_inbox_or_comment_thread

PREREQUISITES:
- Requires post_id (use list_page_posts to find posts first)

NEXT STEPS:
- Use get_inbox_or_comment_thread(item_id, item_type="fb_conv_comments") for full thread

RETURNS: Array of threads with root_comment_id, user name, reply count, has_page_reply status.
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


class ListPostCommentThreadsTool(BaseTool):
    """Tool to list comment threads on a post."""

    def __init__(self, read_service: CommentReadService = None):
        self._read_service = read_service or CommentReadService()

    @property
    def name(self) -> str:
        return "list_post_comment_threads"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "post_id": {
                        "type": "string",
                        "description": "Facebook post ID",
                    },
                    "filter": {
                        "type": "string",
                        "enum": ["all", "has_page_reply", "no_page_reply", "unread"],
                        "default": "all",
                        "description": "Filter threads: 'all', 'has_page_reply', 'no_page_reply', or 'unread'",
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["recent", "top_engagement"],
                        "default": "recent",
                        "description": "Sort order: 'recent' or 'top_engagement'",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                        "description": "Maximum number of threads to return.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "Offset for pagination.",
                    },
                },
                "required": ["post_id", "filter", "sort_by", "limit", "offset"],
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
        """Execute the tool - list comment threads."""
        post_id = arguments.get("post_id")
        if not post_id:
            return {"success": False, "error": "post_id is required"}

        filter_type = arguments.get("filter", "all")
        sort_by = arguments.get("sort_by", "recent")
        limit = arguments.get("limit", 20)
        offset = arguments.get("offset", 0)

        threads, total_count, has_more = (
            await self._read_service.list_comment_threads_by_post(
                conn,
                post_id,
                limit=limit,
                offset=offset,
                filter_type=filter_type,
                sort_by=sort_by,
            )
        )

        # Format threads for output
        formatted_threads = []
        for thread in threads:
            participants = thread.get("participant_scope_users") or []
            if isinstance(participants, str):
                try:
                    participants = json.loads(participants)
                except (json.JSONDecodeError, TypeError):
                    participants = []

            # Get first participant name
            user_name = "Unknown User"
            if (
                participants
                and isinstance(participants, list)
                and len(participants) > 0
            ):
                first_participant = participants[0]
                if isinstance(first_participant, dict):
                    user_name = first_participant.get("name") or "Unknown User"

            formatted_threads.append(
                {
                    "root_comment_id": thread.get("root_comment_id"),
                    "conversation_id": str(thread.get("id")),
                    "user_name": user_name,
                    "message_preview": None,  # Would need to fetch root comment for this
                    "reply_count": thread.get("total_comments", 0)
                    - 1,  # Subtract root comment
                    "has_page_reply": thread.get("has_page_reply", False),
                    "is_unread": thread.get("unread_count", 0) > 0,
                    "like_count": 0,  # Would need to fetch root comment for this
                }
            )

        return {
            "success": True,
            "post_id": post_id,
            "threads": formatted_threads,
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
