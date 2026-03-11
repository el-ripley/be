"""Get Facebook context tool - fetch conversation messages or comment threads.

TOOL_RESULT STRUCTURE (what agent sees):

This tool ALWAYS uses description mode (output_mode="description" hardcoded).
- Media descriptions are embedded in fb_content text
- Content is returned directly in function_output (no human_message created)

function_call_output (output_message.function_output):
   String containing summary text + formatted JSON content (when no media_entries),
   or summary text only (when media_entries exist, content in human_message).

Example for conversation messages (no media):
   "Fetched conversation messages (conv_123) successfully.

   {
     \"type\": \"facebook_conversation_messages\",
     \"conversation_id\": \"conv_123\",
     \"page_info\": {
       \"id\": \"page_123\",
       \"name\": \"My Page\",
       \"category\": \"Retail\"
     },
     \"messages\": [
       {
         \"id\": \"msg_1\",
         \"from\": {\"id\": \"user_123\", \"name\": \"John Doe\"},
         \"message\": \"Hello, I need help\",
         \"created_time\": 1234567890,
         \"attachments\": [
           {
             \"type\": \"image\",
             \"url\": \"https://s3.../image.jpg\",
             \"description\": \"A photo of a red shoe\"
           }
         ]
       }
     ],
     \"pagination\": {
       \"page\": 1,
       \"page_size\": 50,
       \"total_count\": 100,
       \"has_next_page\": true
     }
   }"

Example for comment thread (no media):
   "Fetched comment thread (root_comment_123) successfully.

   {
     \"type\": \"facebook_comment_thread\",
     \"root_comment_id\": \"root_comment_123\",
     \"page_info\": {...},
     \"post_info\": {...},
     \"comments\": [
       {
         \"id\": \"comment_1\",
         \"from\": {\"name\": \"User Name\"},
         \"message\": \"Great product!\",
         \"created_time\": 1234567890
       }
     ],
     \"pagination\": {...}
   }"

Note: This tool is used by main Agent (token efficient with descriptions).
SuggestResponse Agent uses the formatter directly with output_mode="humes_images"
(not through this tool), so it gets human_message with image URLs.
"""

import time
import uuid
from typing import Any, Dict, Optional

import asyncpg

from src.agent.common.api_key_resolver_service import get_system_api_key
from src.agent.common.metadata_types import MessageMetadata
from src.agent.tools.base import ToolCallContext, ToolResult
from src.agent.tools.facebook_query.get_inbox_or_comment_thread.base import (
    FACEBOOK_COMMENT_IMAGE_CONTEXT,
    FACEBOOK_CONVERSATION_IMAGE_CONTEXT,
    FacebookBaseTool,
    get_facebook_comment_system_reminder,
    get_facebook_conversation_system_reminder,
)
from src.agent.tools.facebook_query.get_inbox_or_comment_thread.facebook_formatter import (
    FacebookContentFormatter,
)
from src.api.openai_conversations.schemas import MessageResponse
from src.services.facebook.comments.comment_read_service import CommentReadService
from src.services.facebook.media import MediaAssetService
from src.services.facebook.messages.message_read_service import MessageReadService
from src.utils.logger import get_logger

logger = get_logger()


FACEBOOK_CONTEXT_TOOL_DESCRIPTION = """
Fetch full conversation messages or comment thread content with media.

WHEN TO USE:
- Read complete inbox conversation (item_type="fb_conv_messages")
- Read complete comment thread (item_type="fb_conv_comments")
- When you need actual message/comment content, not just previews
- When you need to see images/attachments in the conversation

PREREQUISITES:
- For inbox: item_id from list_page_conversations
- For comments: item_id (root_comment_id) from list_post_comment_threads

RETURNS: Formatted conversation/thread content with pagination info. Media appears in human_message for AI processing.
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


def _create_human_content_message(
    conv_id: str,
    content: Any,
    metadata: Optional[MessageMetadata] = None,
) -> MessageResponse:
    """Create a human message with multimodal content."""
    human_uuid = str(uuid.uuid4())
    current_time = int(time.time() * 1000)

    return MessageResponse(
        id=human_uuid,
        conversation_id=conv_id,
        sequence_number=0,
        type="message",
        role="user",
        content=content,
        metadata=metadata,
        status="completed",
        created_at=current_time,
        updated_at=current_time,
    )


def normalize_item_type(item_type: str) -> Optional[str]:
    """
    Normalize item_type to internal format.
    Accepts both legacy tab names and the new fb_conv_* enums.
    """
    if item_type in {"conv_messages", "fb_conv_messages"}:
        return "conv_messages"
    if item_type in {"conv_comments", "fb_conv_comments"}:
        return "conv_comments"
    return None


class GetInboxOrCommentThreadTool(FacebookBaseTool):
    """Tool to fetch Facebook conversation messages or comment threads."""

    def __init__(
        self,
        message_read_service: Optional[MessageReadService] = None,
        comment_read_service: Optional[CommentReadService] = None,
        media_asset_service: Optional[MediaAssetService] = None,
        facebook_formatter: Optional[FacebookContentFormatter] = None,
    ):
        self._message_read_service = message_read_service or MessageReadService()
        self._comment_read_service = comment_read_service or CommentReadService()
        self._media_service = media_asset_service or MediaAssetService()
        self._formatter = facebook_formatter or FacebookContentFormatter(
            self._media_service
        )

    @property
    def name(self) -> str:
        return "get_inbox_or_comment_thread"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": FACEBOOK_CONTEXT_TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": (
                            "The Facebook conversation messages id (for inbox messages) or "
                            "The Facebook conversation comments id (for post comment threads)."
                        ),
                    },
                    "item_type": {
                        "type": "string",
                        "enum": ["fb_conv_messages", "fb_conv_comments"],
                        "description": (
                            "Type of content: 'fb_conv_messages' for inbox conversations, "
                            "'fb_conv_comments' for public post comment threads."
                        ),
                    },
                    "page": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 1,
                        "description": "Page number for pagination. Start with 1.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 50,
                        "description": "Number of messages/comments per page. Default 50, max 100.",
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
                "required": [
                    "item_id",
                    "item_type",
                    "page",
                    "limit",
                ],
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
        """Execute the tool - fetch FB data."""
        item_id = arguments.get("item_id")
        item_type = arguments.get("item_type")
        limit = arguments.get("limit", 50)
        page = arguments.get("page", 1)

        # Normalize item_type
        normalized_type = normalize_item_type(item_type)
        if normalized_type is None:
            return {
                "success": False,
                "error": "item_type must be one of ['fb_conv_messages', 'fb_conv_comments']",
            }

        # Normalize pagination inputs
        try:
            target_page = int(page) if page is not None else 1
        except (TypeError, ValueError):
            target_page = 1
        target_page = max(1, target_page)

        try:
            page_size = int(limit) if limit is not None else 50
        except (TypeError, ValueError):
            page_size = 50
        page_size = max(1, min(page_size, 100))

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

        # Fetch raw data from service layer
        fb_data = None
        total_count = 0
        has_next_page = False

        if normalized_type == "conv_messages":
            (
                fb_data,
                total_count,
                has_next_page,
            ) = await self._message_read_service.get_conversation_messages_paginated(
                conn=conn,
                conversation_id=item_id,
                page=target_page,
                page_size=page_size,
            )
            if fb_data:
                # Ensure media assets are populated
                await self._media_service.ensure_conversation_assets(
                    conn=conn,
                    user_id=user_id,
                    fb_conversation_id=item_id,
                    fb_data=fb_data,
                    should_describe=should_describe,
                    user_api_key=user_api_key,
                    parent_agent_response_id=context.agent_response_id,
                    conversation_id=context.conv_id,
                    branch_id=context.branch_id,
                )
                # Format for AI consumption (always use description mode for Agent tools)
                formatted_result = self._formatter.format_conversation_messages(
                    fb_data,
                    item_id,
                    is_active_tab=False,
                    output_mode="description",
                    page=target_page,
                    page_size=page_size,
                    total_count=total_count,
                    has_next_page=has_next_page,
                )
                if formatted_result:
                    fb_data = formatted_result

        elif normalized_type == "conv_comments":
            (
                fb_data,
                total_count,
                has_next_page,
            ) = await self._comment_read_service.get_comment_thread_paginated(
                conn=conn,
                root_comment_id=item_id,
                page=target_page,
                page_size=page_size,
            )
            if fb_data:
                # Ensure media assets are populated
                await self._media_service.ensure_comment_assets(
                    conn,
                    user_id,
                    item_id,
                    fb_data,
                    should_describe=should_describe,
                    user_api_key=user_api_key,
                    parent_agent_response_id=context.agent_response_id,
                    conversation_id=context.conv_id,
                    branch_id=context.branch_id,
                )
                # Format for AI consumption (always use description mode for Agent tools)
                root_comment_id = fb_data.get("root_comment_id", item_id)
                formatted_result = self._formatter.format_conversation_comments(
                    fb_data,
                    root_comment_id,
                    is_active_tab=False,
                    output_mode="description",
                    page=target_page,
                    page_size=page_size,
                    total_count=total_count,
                    has_next_page=has_next_page,
                )
                if formatted_result:
                    fb_data = formatted_result

        if not fb_data:
            return {
                "success": False,
                "item_id": item_id,
                "item_type": item_type,
                "error": "No content found for the requested item",
            }

        total_pages = (
            (total_count + page_size - 1) // page_size if total_count > 0 else 1
        )

        return {
            "success": True,
            "item_id": item_id,
            "item_type": item_type,
            "normalized_item_type": normalized_type,
            "fb_content": fb_data.get("fb_content"),
            "media_entries": fb_data.get("media_entries") or [],
            "page": target_page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next_page": has_next_page,
            "next_page": target_page + 1 if has_next_page else None,
        }

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Process raw result into ToolResult."""
        # Extract content and media from result
        fb_content = None
        media_entries = []

        if isinstance(raw_result, dict):
            # Create a copy to avoid mutating the original
            tool_result = raw_result.copy()
            fb_content = tool_result.pop("fb_content", None)
            media_entries = tool_result.pop("media_entries", []) or []
        else:
            tool_result = raw_result

        # Build metadata
        fb_meta = self.build_facebook_metadata(tool_result, context.arguments)

        # Determine if we should include content directly in function_output
        # When no media_entries (description mode), include fb_content in output
        # When media_entries exist (humes_images mode), content goes in human_message
        include_content_in_output = bool(fb_content and not media_entries)

        # Create function_call_output message
        output_message = _create_function_call_output(
            conv_id=context.conv_id,
            call_id=context.call_id,
            function_output=self.build_facebook_summary(
                tool_result=tool_result,
                tool_args=context.arguments,
                fb_content=fb_content,
                media_entries=media_entries,
                include_content_in_output=include_content_in_output,
            ),
            metadata=fb_meta,
        )

        # Create human message if content exists and tool succeeded
        # NOTE: When output_mode="description", media_entries is empty, so no human_message needed
        human_message = None
        if (
            (fb_content or media_entries)
            and isinstance(tool_result, dict)
            and tool_result.get("success")
        ):
            # Only create human_message if we have media_entries (humes_images mode)
            # For description mode, media is embedded in fb_content JSON
            if media_entries:
                normalized_type = tool_result.get(
                    "normalized_item_type"
                ) or context.arguments.get("item_type")
                system_reminder = None
                image_context = None
                if normalized_type in {"conv_comments", "fb_conv_comments"}:
                    system_reminder = get_facebook_comment_system_reminder()
                    image_context = FACEBOOK_COMMENT_IMAGE_CONTEXT
                elif normalized_type in {"conv_messages", "fb_conv_messages"}:
                    system_reminder = get_facebook_conversation_system_reminder()
                    image_context = FACEBOOK_CONVERSATION_IMAGE_CONTEXT

                human_content = []
                if system_reminder:
                    human_content.append(
                        {"type": "input_text", "text": system_reminder}
                    )
                if fb_content:
                    human_content.append({"type": "input_text", "text": fb_content})
                if image_context and media_entries:
                    human_content.append({"type": "input_text", "text": image_context})
                human_content.extend(media_entries)
                human_message = _create_human_content_message(
                    conv_id=context.conv_id,
                    content=human_content,
                    metadata=fb_meta,
                )

        return ToolResult(
            output_message=output_message,
            human_message=human_message,
            metadata=fb_meta,
        )
