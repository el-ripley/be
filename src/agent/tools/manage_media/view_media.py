"""Tool to view image URLs by loading them into context.
Agent uses this when description is insufficient and needs to see actual images.

TOOL_RESULT STRUCTURE (what agent sees):

On success, function_output is array of image objects:
   [
     {
       "type": "input_image",
       "image_url": "https://elripley.s3.ap-southeast-2.amazonaws.com/..."
     },
     {
       "type": "input_image",
       "image_url": "https://elripley.s3.ap-southeast-2.amazonaws.com/..."
     }
   ]

On error, function_output is JSON string:
   "{\"success\": false, \"error\": \"No media assets found for the provided IDs\"}"

Note: Images are loaded directly in function_output as array of objects.
Each image consumes significant context tokens (~200-1000).
"""

import json
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.database.postgres.connection import (
    get_agent_reader_connection,
    get_suggest_response_reader_connection,
)
from src.database.postgres.repositories.media_assets_queries import (
    get_media_assets_by_ids,
)
from src.utils.logger import get_logger

logger = get_logger()

# Valid S3 domain for elripley
ELRIPLEY_S3_DOMAIN = "elripley.s3.ap-southeast-2.amazonaws.com"

TOOL_DESCRIPTION = """
View image URLs by loading them into context to use LLM's vision capabilities for detailed image analysis.

WHEN TO USE (ONLY):
- When you have a specific reason to doubt/question the existing description of a media file
- When you need to use LLM's vision capabilities to carefully examine an image for detailed analysis
- When the description exists but seems inaccurate or insufficient for the current task

WHEN NOT TO USE:
- If images are already attached in the current message as input_image (you can already see them with your vision capabilities)
- If media files in data have no description (description: null), use describe_media tool instead
- The describe_media tool will generate and save descriptions to database, so next time you fetch the data, descriptions will already be available
- Do NOT use this tool just because description is missing - use describe_media for that

IMPORTANT:
- Each image consumes significant context tokens (~200-1000)
- Only use when you have a legitimate reason to question existing descriptions
- Requires media_id(s) from media objects in the data (e.g., page_info.avatar.media_id, attachments[].media_id)
- Media files must be managed by the system (have media_id)
- This tool is for vision-based analysis, not for generating descriptions

RETURNS: On success, array of image objects. On error, JSON string with error details.
"""


class ViewMediaTool(BaseTool):
    @property
    def name(self) -> str:
        return "view_media"

    @property
    def definition(self) -> Dict[str, Any]:
        base_def = {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "media_ids": {
                        "type": "array",
                        "description": "List of media objects with index and media_id",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {
                                    "type": "integer",
                                    "description": "Index of the media",
                                },
                                "media_id": {
                                    "type": "string",
                                    "description": "Media asset UUID. These media files are managed by the system.",
                                },
                            },
                            "required": ["index", "media_id"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["media_ids"],
                "additionalProperties": False,
            },
            "strict": True,
        }
        return self._apply_description_override(base_def)

    async def execute(
        self,
        conn: Optional[asyncpg.Connection],
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        media_items = arguments.get("media_ids", [])

        if not isinstance(media_items, list) or len(media_items) == 0:
            return {
                "success": False,
                "error": "media_ids must be a non-empty list",
            }

        # Validate input structure and extract media_ids
        media_id_list = []
        index_map = {}  # Map media_id -> index for maintaining order

        for item in media_items:
            if not isinstance(item, dict):
                return {
                    "success": False,
                    "error": "Each item in media_ids must be an object with 'index' and 'media_id'",
                }

            index = item.get("index")
            media_id = item.get("media_id", "")

            if not isinstance(index, int):
                return {
                    "success": False,
                    "error": f"Each item must have an integer 'index', got: {type(index).__name__}",
                    "index": index,
                }

            if not isinstance(media_id, str) or not media_id:
                return {
                    "success": False,
                    "error": "Each item must have a non-empty string 'media_id'",
                    "index": index,
                }

            media_id_list.append(media_id)
            index_map[media_id] = index

        async def _run(c: asyncpg.Connection) -> Any:
            media_assets = await get_media_assets_by_ids(
                conn=c, media_ids=media_id_list, user_id=context.user_id
            )

            if not media_assets:
                return {
                    "success": False,
                    "error": "No media assets found for the provided IDs",
                }

            # Build map of media_id -> media record
            media_map = {str(asset.get("id")): asset for asset in media_assets}

            # Validate and collect URLs, maintaining index order
            validated_urls = []
            errors = []

            for media_id in media_id_list:
                media_id_str = str(media_id)
                index = index_map.get(media_id)
                asset = media_map.get(media_id_str)

                if not asset:
                    errors.append(
                        {
                            "index": index,
                            "media_id": media_id_str,
                            "error": "Media asset not found or not owned by user",
                        }
                    )
                    continue

                s3_url = asset.get("s3_url")
                if not s3_url:
                    errors.append(
                        {
                            "index": index,
                            "media_id": media_id_str,
                            "error": "No S3 URL found for media asset",
                        }
                    )
                    continue

                # Validate URL format
                if not s3_url.startswith("https://"):
                    errors.append(
                        {
                            "index": index,
                            "media_id": media_id_str,
                            "error": "Invalid URL format: Must start with https://",
                            "url": s3_url,
                        }
                    )
                    continue

                # Validate URL is from elripley S3
                parsed = urlparse(s3_url)
                if parsed.netloc != ELRIPLEY_S3_DOMAIN:
                    errors.append(
                        {
                            "index": index,
                            "media_id": media_id_str,
                            "error": f"Invalid S3 domain: Must be elripley S3 URL (domain: {ELRIPLEY_S3_DOMAIN})",
                            "url": s3_url,
                            "actual_domain": parsed.netloc,
                        }
                    )
                    continue

                validated_urls.append(
                    {"index": index, "media_id": media_id_str, "url": s3_url}
                )

            if errors:
                return {
                    "success": False,
                    "error": "Some media assets failed validation",
                    "errors": errors,
                    "validated_count": len(validated_urls),
                }

            # Sort by index to maintain order
            validated_urls.sort(key=lambda x: x["index"])

            return {
                "success": True,
                "image_urls": validated_urls,
            }

        # Detect: running in suggest_response context? (Facebook conversation RLS)
        is_suggest_response = (
            context.fb_conversation_type is not None
            and context.fb_conversation_id is not None
            and context.fan_page_id is not None
        )

        try:
            if conn is not None:
                return await _run(conn)

            if is_suggest_response:
                # Use suggest_response connection with RLS
                async with get_suggest_response_reader_connection(
                    user_id=context.user_id,
                    conversation_type=context.fb_conversation_type,
                    conversation_id=context.fb_conversation_id,
                    fan_page_id=context.fan_page_id,
                    page_scope_user_id=context.page_scope_user_id,
                ) as c:
                    return await _run(c)
            else:
                # General agent connection
                async with get_agent_reader_connection(context.user_id) as c:
                    return await _run(c)
        except Exception as e:
            logger.error(f"Error in view_media: {str(e)}")
            return {"success": False, "error": f"Internal error: {str(e)}"}

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Process raw result into ToolResult."""
        output_uuid = str(uuid.uuid4())
        current_time = int(time.time() * 1000)

        if isinstance(raw_result, dict) and raw_result.get("success"):
            # Success case: return array of image objects only (no input_text)
            image_urls = raw_result.get("image_urls", [])
            function_output = []

            for item in image_urls:
                function_output.append(
                    {
                        "type": "input_image",
                        "image_url": item["url"],
                    }
                )
        else:
            # Error case: return JSON string
            error_data = (
                raw_result
                if isinstance(raw_result, dict)
                else {"error": str(raw_result)}
            )
            function_output = json.dumps(error_data)

        output_message = MessageResponse(
            id=output_uuid,
            conversation_id=context.conv_id,
            sequence_number=0,
            type="function_call_output",
            role="tool",
            content=None,
            call_id=context.call_id,
            function_output=function_output,
            status="completed",
            metadata=None,
            created_at=current_time,
            updated_at=current_time,
        )

        # No human_message - everything is in function_output
        return ToolResult(
            output_message=output_message,
            human_message=None,
            metadata=None,
        )
