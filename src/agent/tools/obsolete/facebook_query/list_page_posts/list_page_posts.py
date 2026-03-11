"""List page posts tool - list posts from a Facebook page.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):
   {
     "success": true,
     "page_id": str,
     "posts": [
       {
         "post_id": str,  # Use with get_post_details or list_post_comment_threads
         "message_preview": str | None,  # First 200 chars or None
         "created_time": int | None,  # timestamp
         "reaction_total": int,  # Total reactions count
         "comment_count": int,
         "share_count": int,
         "has_media": bool  # True if post has photos/videos
       }
       // ... paginated results
     ],
     "total_count": int,
     "limit": int,
     "offset": int,
     "has_more": bool
   }
"""

import time
import uuid
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.services.facebook.posts.post_read_service import PostReadService
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
List posts from a Facebook page with engagement metrics.

WHEN TO USE:
- Browse page content overview
- Find posts by engagement level (top or recent)
- Before using get_post_details for a specific post
- When user asks about page activity or post performance

PREREQUISITES:
- Requires page_id (which you should already have from system prompt)

NEXT STEPS:
- Use get_post_details(post_id) for full content and media
- Use list_post_comment_threads(post_id) to see comments

RETURNS: Array of posts with id, message preview, engagement counts, timestamps.
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


class ListPagePostsTool(BaseTool):
    """Tool to list posts from a Facebook page."""

    def __init__(self, read_service: PostReadService = None):
        self._read_service = read_service or PostReadService()

    @property
    def name(self) -> str:
        return "list_page_posts"

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
                    "sort_by": {
                        "type": "string",
                        "enum": ["recent", "top_engagement"],
                        "default": "recent",
                        "description": "Sort order: 'recent' for newest first, 'top_engagement' for most engagement first",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                        "description": "Maximum number of posts to return.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "Offset for pagination.",
                    },
                },
                "required": ["page_id", "sort_by", "limit", "offset"],
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
        """Execute the tool - list page posts."""
        page_id = arguments.get("page_id")
        if not page_id:
            return {"success": False, "error": "page_id is required"}

        sort_by = arguments.get("sort_by", "recent")
        limit = arguments.get("limit", 20)
        offset = arguments.get("offset", 0)
        time_range_days = arguments.get("time_range_days")

        posts, total_count, has_more = await self._read_service.list_posts_by_page(
            conn,
            page_id,
            limit=limit,
            offset=offset,
            time_range_days=time_range_days,
            sort_by=sort_by,
        )

        # Format posts for output
        formatted_posts = []
        for post in posts:
            formatted_posts.append(
                {
                    "post_id": post.get("id"),
                    "message_preview": (
                        (post.get("message") or "")[:200]
                        if post.get("message")
                        else None
                    ),
                    "created_time": post.get("facebook_created_time"),
                    "reaction_total": post.get("reaction_total_count", 0),
                    "comment_count": post.get("comment_count", 0),
                    "share_count": post.get("share_count", 0),
                    "has_media": bool(
                        post.get("photo_link")
                        or post.get("video_link")
                        or post.get("full_picture")
                    ),
                }
            )

        return {
            "success": True,
            "page_id": page_id,
            "posts": formatted_posts,
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
