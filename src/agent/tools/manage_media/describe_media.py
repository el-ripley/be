"""Tool to generate AI descriptions for media assets.

Allows agent to generate descriptions for images using MediaDescriptionService
before using them in prompts.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):
   {
     "success": true,
     "results": [
       {
         "media_id": "uuid-1",
         "description": "A red shoe on white background",
         "status": "generated"
       },
       {
         "media_id": "uuid-2",
         "description": "Previous description",
         "status": "skipped"  # Skipped because description already exists (unless force=true)
       },
       {
         "media_id": "uuid-3",
         "error": "Failed to generate description",
         "status": "failed"
       }
     ],
     "generated_count": 1,
     "skipped_count": 1,
     "failed_count": 1
   }
"""

import uuid
import time
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.agent.common.api_key_resolver_service import get_system_api_key
from src.services.media.media_description_service import MediaDescriptionService
from src.database.postgres.repositories.media_assets_queries import (
    get_media_assets_by_ids,
    update_media_description_by_ai,
)
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
Generate AI descriptions for media assets (images) that exist in data but lack descriptions.

WHEN TO USE:
- When processing data (e.g., Facebook conversations, page info) that contains media objects with only URL and media_id but no description (description: null)
- When media files in the data need descriptions to improve reasoning and context understanding
- Before using media in prompts when descriptions would enhance the agent's understanding of the content

CONTEXT:
- Media in data structures (conversations, page info, attachments) may have URLs and media_ids but no descriptions
- These media files without descriptions don't help with reasoning
- This tool generates descriptions so the agent can better utilize the data

PREREQUISITES:
- Requires media_id(s) from media objects in the data (e.g., page_info.avatar.media_id, attachments[].media_id)

RETURNS: Updated media info with generated descriptions.
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


class DescribeMediaTool(BaseTool):
    """Tool to generate AI descriptions for media assets."""

    def __init__(
        self,
        description_service: MediaDescriptionService = None,
    ):
        self._description_service = description_service or MediaDescriptionService()

    @property
    def name(self) -> str:
        return "describe_media"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "media_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of media asset UUIDs to describe",
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, regenerate descriptions even if they already exist. Default false skips media with existing descriptions.",
                    },
                },
                "required": ["media_ids"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - generate descriptions for media assets."""
        media_ids = arguments.get("media_ids", [])
        force = arguments.get("force", False)

        if not media_ids:
            return {
                "success": False,
                "error": "media_ids is required and must not be empty",
            }

        if not isinstance(media_ids, list):
            return {
                "success": False,
                "error": "media_ids must be an array",
            }

        try:
            # Get media assets with ownership validation
            media_assets = await get_media_assets_by_ids(
                conn=conn, media_ids=media_ids, user_id=context.user_id
            )

            if not media_assets:
                return {
                    "success": False,
                    "error": "No media assets found for the provided IDs",
                }

            # Build map of media_id -> media record
            media_map = {str(asset.get("id")): asset for asset in media_assets}

            # Filter: skip if has description and !force
            items_to_describe = []
            skipped_items = []

            for media_id in media_ids:
                media_id_str = str(media_id)
                asset = media_map.get(media_id_str)

                if not asset:
                    continue  # Not found or not owned by user

                has_description = bool(asset.get("description"))
                if has_description and not force:
                    skipped_items.append(
                        {
                            "media_id": media_id_str,
                            "description": asset.get("description"),
                            "status": "skipped",
                        }
                    )
                else:
                    s3_url = asset.get("s3_url")
                    if not s3_url:
                        skipped_items.append(
                            {
                                "media_id": media_id_str,
                                "error": "No S3 URL found for media asset",
                                "status": "failed",
                            }
                        )
                    else:
                        items_to_describe.append(
                            {
                                "media_id": media_id_str,
                                "url": s3_url,
                                "context": "user uploaded image",
                            }
                        )

            # If nothing to describe, return early
            if not items_to_describe:
                return {
                    "success": True,
                    "results": skipped_items,
                    "generated_count": 0,
                    "skipped_count": len(skipped_items),
                    "failed_count": 0,
                }

            # Get system API key
            try:
                api_key = get_system_api_key()
            except Exception as e:
                logger.error(f"Failed to get system API key: {e}")
                return {
                    "success": False,
                    "error": f"Failed to get system API key: {str(e)}",
                }

            # Generate descriptions using MediaDescriptionService
            descriptions = await self._description_service.describe_batch(
                conn=conn,
                items=items_to_describe,
                api_key=api_key,
                user_id=context.user_id,
                parent_agent_response_id=context.agent_response_id,
                conversation_id=context.conv_id,
                branch_id=context.branch_id,
            )

            # Update media assets with descriptions
            results = skipped_items.copy()
            generated_count = 0
            failed_count = 0

            for item in items_to_describe:
                media_id = item["media_id"]
                description = descriptions.get(media_id)

                if description:
                    # Update database with description
                    try:
                        updated = await update_media_description_by_ai(
                            conn=conn,
                            media_id=media_id,
                            description=description,
                            description_model=self._description_service.model,
                            user_id=context.user_id,
                        )

                        if updated:
                            results.append(
                                {
                                    "media_id": media_id,
                                    "description": description,
                                    "status": "generated",
                                }
                            )
                            generated_count += 1
                        else:
                            results.append(
                                {
                                    "media_id": media_id,
                                    "error": "Failed to update media asset",
                                    "status": "failed",
                                }
                            )
                            failed_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to update description for media {media_id}: {e}"
                        )
                        results.append(
                            {
                                "media_id": media_id,
                                "error": f"Update failed: {str(e)}",
                                "status": "failed",
                            }
                        )
                        failed_count += 1
                else:
                    results.append(
                        {
                            "media_id": media_id,
                            "error": "Failed to generate description",
                            "status": "failed",
                        }
                    )
                    failed_count += 1

            return {
                "success": True,
                "results": results,
                "generated_count": generated_count,
                "skipped_count": len(skipped_items),
                "failed_count": failed_count,
            }

        except Exception as e:
            logger.error(f"Error in describe_media: {str(e)}")
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
