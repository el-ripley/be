"""Get post details tool - get detailed information about a Facebook post.

TOOL_RESULT STRUCTURE (what agent sees):

- Media descriptions are embedded in post_content text
- Content is returned directly in function_output as a parsed JSON object
- No human_message is created, everything is in function_output

function_call_output (output_message.function_output):
   {
     "type": "facebook_post",
     "id": "post_123",
     "post_message": "Check out our new product!",
     "post_photo": {
       "type": "post_image",
       "url": "https://s3.../image.jpg",
       "description": "A red shoe on white background",
       "media_id": "uuid-here"
     },
     "permalink_url": "https://facebook.com/...",
     "status_type": "photo",
     "is_published": true,
     "created_time": 1234567890,
     "reactions": {
       "total": 150,
       "like": 100,
       "love": 30,
       "haha": 10,
       "wow": 5,
       "sad": 3,
       "angry": 2,
       "care": 0
     },
     "comment_count": 25,
     "share_count": 10,
     "video_link": null | "https://...",
     "top_reactors": [...]  # Only if include_top_reactors=true
   }
"""

import json
import time
import uuid
from typing import Any, Dict, Optional

import asyncpg

from src.agent.common.api_key_resolver_service import get_system_api_key
from src.agent.common.metadata_types import MessageMetadata
from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.agent.tools.facebook_query.get_post_details.formatter import (
    format_post_details,
)
from src.agent.tools.facebook_query.get_post_details.multimodal import (
    MultimodalContentBuilder,
)
from src.api.openai_conversations.schemas import MessageResponse
from src.services.facebook.media import MediaAssetService
from src.services.facebook.posts.post_read_service import PostReadService
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
Get complete information about a Facebook post including full content and media.

WHEN TO USE:
- View full post message (not just preview)
- See post images, videos, or attachments
- Get detailed engagement breakdown (reaction types, shares)
- Analyze a specific post in depth

PREREQUISITES:
- Requires post_id (use list_page_posts to find posts first)

RETURNS: Full post content with embedded media descriptions and engagement breakdown.
"""


def _create_function_call_output(
    conv_id: str,
    call_id: str,
    function_output: Any,
    metadata: Optional[MessageMetadata] = None,
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
        metadata=metadata,
        created_at=current_time,
        updated_at=current_time,
    )


class GetPostDetailsTool(BaseTool):
    """Tool to get detailed information about a Facebook post."""

    def __init__(
        self,
        read_service: PostReadService = None,
        media_asset_service: MediaAssetService = None,
    ):
        self._read_service = read_service or PostReadService()
        self._media_service = media_asset_service or MediaAssetService()

    @property
    def name(self) -> str:
        return "get_post_details"

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
                    "include_top_reactors": {
                        "type": "boolean",
                        "default": False,
                        "description": "Whether to include list of top reactors",
                    },
                    "should_describe_media": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "If true, mirror images to S3 and generate AI descriptions. "
                            "If false, return Facebook URLs without descriptions."
                        ),
                    },
                },
                "required": ["post_id"],
                "additionalProperties": False,
            },
            "strict": False,
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - get post details."""
        post_id = arguments.get("post_id")
        if not post_id:
            return {"success": False, "error": "post_id is required"}

        include_top_reactors = arguments.get("include_top_reactors", False)

        post_data = await self._read_service.get_post_with_engagement(
            conn,
            post_id,
            include_top_reactors=include_top_reactors,
        )

        if not post_data:
            return {"success": False, "error": f"Post {post_id} not found"}

        # Get should_describe_media param
        should_describe = arguments.get("should_describe_media", True)
        user_id = context.user_id

        # Get system API key if needed for description
        user_api_key = None
        if should_describe:
            try:
                user_api_key = get_system_api_key()
            except Exception as e:
                logger.warning(
                    f"Failed to get system API key: {e}. "
                    "Media description will be skipped."
                )
                should_describe = False

        # Ensure media assets are populated
        await self._media_service.ensure_post_assets(
            conn=conn,
            user_id=user_id,
            post_id=post_id,
            post_data=post_data,
            should_describe=should_describe,
            user_api_key=user_api_key,
            parent_agent_response_id=context.agent_response_id,
            conversation_id=context.conv_id,
            branch_id=context.branch_id,
        )

        # Always use description mode
        builder = MultimodalContentBuilder()
        post_content = format_post_details(
            post_data, builder, output_mode="description"
        )

        return {
            "success": True,
            "post_id": post_id,
            "post_content": post_content,
        }

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Process raw result into ToolResult."""
        post_content = None

        if isinstance(raw_result, dict):
            tool_result = raw_result.copy()
            post_content = tool_result.pop("post_content", None)
        else:
            tool_result = raw_result

        # Build function_output
        # When success=True, return parsed JSON object directly (not wrapped in post_details key)
        if isinstance(tool_result, dict):
            if tool_result.get("success") and post_content:
                # Parse JSON string to object and return directly
                try:
                    function_output = json.loads(post_content)
                except (json.JSONDecodeError, TypeError):
                    # If parsing fails, return as is
                    function_output = post_content
            else:
                # Error case: return error structure
                function_output = tool_result.copy()
        else:
            function_output = {"error": str(tool_result)}

        # Create function_call_output message
        output_message = _create_function_call_output(
            conv_id=context.conv_id,
            call_id=context.call_id,
            function_output=function_output,
        )

        # No human_message - everything is in function_output
        return ToolResult(
            output_message=output_message,
            human_message=None,
            metadata=None,
        )
