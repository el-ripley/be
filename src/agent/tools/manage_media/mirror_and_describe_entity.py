"""Tool to mirror and describe media for Facebook entities.

Allows agent to mirror images from Facebook to S3 and generate AI descriptions
for entities (posts, comments, messages, pages, etc.) after querying with sql_query tool.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):
   {
     "success": true,
     "results": [
       {
         "owner_type": "post",
         "owner_id": "123456789_987654321",
         "status": "completed",
         "media": [
           {
             "field_name": "photo_link",
             "media_id": "uuid-here",
             "s3_url": "https://s3.../image.jpg",
             "description": "A red product on white background",
             "action": "mirrored_and_described"  # or "described", "skipped", "failed"
           }
         ]
       }
     ],
     "summary": {
       "total_entities": 2,
       "completed": 1,
       "failed": 1,
       "media_mirrored": 1,
       "media_described": 1,
       "media_skipped": 0
     }
   }
"""

import time
import uuid
from typing import Any, Dict

import asyncpg

from src.agent.common.api_key_resolver_service import get_system_api_key
from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.services.facebook.media.entity_media_service import EntityMediaService
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
Mirror images from Facebook to S3 and generate AI descriptions for Facebook entities.

WHEN TO USE:
- After querying Facebook data with sql_query tool and finding entities with media URLs
- When you need to process images from posts, comments, messages, pages, or users
- When media needs descriptions for better reasoning and context understanding

PREREQUISITES:
- Requires owner_type and owner_id from entities found in database queries
- Supported owner_types: fan_page, page_scope_user, post, comment, message, facebook_conversation

RETURNS: Detailed results per entity with media mirror and description status.
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


class MirrorAndDescribeEntityMediaTool(BaseTool):
    """Tool to mirror and describe media for Facebook entities."""

    def __init__(self, entity_media_service: EntityMediaService = None):
        self._entity_media_service = entity_media_service or EntityMediaService()

    @property
    def name(self) -> str:
        return "mirror_and_describe_entity_media"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "entities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "owner_type": {
                                    "type": "string",
                                    "enum": [
                                        "fan_page",
                                        "page_scope_user",
                                        "post",
                                        "comment",
                                        "message",
                                        "facebook_conversation",
                                    ],
                                    "description": "Type of Facebook entity",
                                },
                                "owner_id": {
                                    "type": "string",
                                    "description": "ID of the entity (e.g., post_id, comment_id, page_id)",
                                },
                            },
                            "required": ["owner_type", "owner_id"],
                            "additionalProperties": False,
                        },
                        "description": "List of entities to process media for",
                    },
                    "force_describe": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "If true, regenerate descriptions even if they already exist. "
                            "Default false skips media with existing descriptions. "
                            "Note: Media will not be re-mirrored if already in S3."
                        ),
                    },
                },
                "required": ["entities"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - mirror and describe media for entities."""
        entities = arguments.get("entities", [])
        force_describe = arguments.get("force_describe", False)

        if not entities:
            return {
                "success": False,
                "error": "entities is required and must not be empty",
            }

        if not isinstance(entities, list):
            return {
                "success": False,
                "error": "entities must be an array",
            }

        user_id = context.user_id

        # Get system API key for description
        try:
            api_key = get_system_api_key()
        except Exception as e:
            logger.error(f"Failed to get system API key: {e}")
            return {
                "success": False,
                "error": f"Failed to get system API key: {str(e)}",
            }

        # Validate and filter entities
        valid_entities = []
        invalid_results = []
        for entity in entities:
            owner_type = entity.get("owner_type")
            owner_id = entity.get("owner_id")

            if not owner_type or not owner_id:
                invalid_results.append(
                    {
                        "owner_type": owner_type,
                        "owner_id": owner_id,
                        "status": "failed",
                        "error": "owner_type and owner_id are required",
                    }
                )
                continue

            valid_entities.append({"owner_type": owner_type, "owner_id": str(owner_id)})

        # Process all valid entities in batch
        batch_results = []
        if valid_entities:
            batch_results = await self._entity_media_service.process_entities_batch(
                conn=conn,
                user_id=user_id,
                entities=valid_entities,
                force_describe=force_describe,
                api_key=api_key,
                parent_agent_response_id=context.agent_response_id,
                conversation_id=context.conv_id,
                branch_id=context.branch_id,
            )

        # Combine valid and invalid results
        results = invalid_results + batch_results

        # Calculate summary
        summary = {
            "total_entities": len(entities),
            "completed": 0,
            "failed": 0,
            "media_mirrored": 0,
            "media_described": 0,
            "media_skipped": 0,
        }

        for result in results:
            if result["status"] == "completed":
                summary["completed"] += 1
                # Count media actions
                for media_item in result.get("media", []):
                    action = media_item.get("action", "")
                    if action == "mirrored" or action == "mirrored_and_described":
                        summary["media_mirrored"] += 1
                    if action == "described" or action == "mirrored_and_described":
                        summary["media_described"] += 1
                    if action == "skipped":
                        summary["media_skipped"] += 1
            else:
                summary["failed"] += 1

        return {
            "success": True,
            "results": results,
            "summary": summary,
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
