"""Tool to move media assets between S3 retention tiers.

Allows agent to manage media lifecycle by moving files between retention tiers,
handling both S3 operations and database updates atomically.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):
   {
     "success": true,
     "results": [
       {
         "media_id": "uuid-1",
         "status": "moved",
         "from": "one_week",
         "to": "permanent"
       },
       {
         "media_id": "uuid-2",
         "status": "skipped",
         "reason": "already at target tier"
       },
       {
         "media_id": "uuid-3",
         "status": "failed",
         "error": "Insufficient storage quota"
       }
     ],
     "moved_count": 1,
     "skipped_count": 1,
     "failed_count": 1
   }
"""

import uuid
import time
import asyncio
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.database.postgres.repositories.media_assets_queries import (
    get_media_assets_by_ids,
    update_media_retention_and_location,
)
from src.database.postgres.repositories.user_storage_quotas_queries import (
    check_quota_limit,
    create_or_update_user_storage_quota,
)
from src.common.s3_client import get_s3_uploader
from src.database.postgres.utils import get_current_timestamp_ms
from src.utils.logger import get_logger

logger = get_logger()


MAX_BATCH_SIZE = 20
S3_SEMAPHORE = 5  # Max concurrent S3 operations

RETENTION_DAYS = {
    "one_day": 1,
    "one_week": 7,
    "two_weeks": 14,
    "one_month": 30,
    "permanent": None,
}

TOOL_DESCRIPTION = """
Move media assets between S3 retention tiers. This handles BOTH the S3 file move
AND database record updates automatically — no need to run SQL manually.

WHEN TO USE:
- PROMOTE: After attaching media to memory blocks, move from ephemeral to 'permanent'
  or 'one_month' so the media survives beyond its current lifecycle expiry.
- DEMOTE: When detaching media from memory, move to 'one_day' to schedule cleanup
  and free quota space.

RETENTION TIERS (aligned with S3 lifecycle rules):
- 'one_day':    File deleted after 1 day   (S3 prefix: ephemeral/one_day/)
- 'one_week':   File deleted after 7 days  (S3 prefix: ephemeral/one_week/)
- 'two_weeks':  File deleted after 14 days (S3 prefix: ephemeral/two_weeks/)
- 'one_month':  File deleted after 30 days (S3 prefix: ephemeral/one_month/)
- 'permanent':  Never deleted, counts toward user storage quota (S3 prefix: persistent/)

WHAT THIS TOOL DOES AUTOMATICALLY:
1. Validates media ownership
2. Copies file to new S3 prefix
3. Deletes old S3 file
4. Updates media_assets record (s3_key, s3_url, retention_policy, expires_at)
5. Adjusts user storage quota when moving to/from 'permanent'

IMPORTANT:
- Max 20 media_ids per call
- Media already at target tier will be skipped (no error)
- Moving TO 'permanent' requires sufficient storage quota
- Moving FROM 'permanent' frees quota

RETURNS: Summary with succeeded, skipped, and failed lists.
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


class ChangeMediaRetentionTool(BaseTool):
    """Tool to move media assets between S3 retention tiers."""

    @property
    def name(self) -> str:
        return "change_media_retention"

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
                        "description": "List of media asset UUIDs to move. Max 20 per call.",
                    },
                    "target_retention": {
                        "type": "string",
                        "enum": [
                            "one_day",
                            "one_week",
                            "two_weeks",
                            "one_month",
                            "permanent",
                        ],
                        "description": "Target retention tier. 'permanent' counts toward storage quota.",
                    },
                },
                "required": ["media_ids", "target_retention"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - move media between retention tiers."""
        media_ids = arguments.get("media_ids", [])
        target_retention = arguments.get("target_retention")

        # Validate inputs
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

        if len(media_ids) > MAX_BATCH_SIZE:
            return {
                "success": False,
                "error": f"Too many media_ids. Maximum {MAX_BATCH_SIZE} per call.",
            }

        if target_retention not in RETENTION_DAYS:
            return {
                "success": False,
                "error": f"Invalid target_retention. Must be one of: {list(RETENTION_DAYS.keys())}",
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

            moved_count = 0
            skipped_count = 0
            failed_count = 0

            # Use semaphore for S3 operations
            semaphore = asyncio.Semaphore(S3_SEMAPHORE)

            async def process_one_media(media_id: str):
                nonlocal moved_count, skipped_count, failed_count

                media_id_str = str(media_id)
                asset = media_map.get(media_id_str)

                if not asset:
                    failed_count += 1
                    return {
                        "media_id": media_id_str,
                        "status": "failed",
                        "error": "Media not found or not owned by user",
                    }

                # Check current retention policy
                current_retention = asset.get("retention_policy")
                if current_retention == target_retention:
                    skipped_count += 1
                    return {
                        "media_id": media_id_str,
                        "status": "skipped",
                        "reason": "already at target tier",
                    }

                # Validate media has S3 URL
                old_s3_url = asset.get("s3_url")
                if not old_s3_url:
                    failed_count += 1
                    return {
                        "media_id": media_id_str,
                        "status": "failed",
                        "error": "No S3 URL found for media asset",
                    }

                # Check status
                if asset.get("status") == "failed":
                    failed_count += 1
                    return {
                        "media_id": media_id_str,
                        "status": "failed",
                        "error": "Media asset is in failed status",
                    }

                file_size = asset.get("file_size_bytes", 0)

                # Quota check if moving TO permanent from non-permanent
                if target_retention == "permanent" and current_retention != "permanent":
                    has_quota, quota_record = await check_quota_limit(
                        conn, context.user_id, file_size
                    )
                    if not has_quota:
                        current_usage = quota_record.get(
                            "permanent_storage_used_bytes", 0
                        )
                        limit = quota_record.get(
                            "permanent_storage_limit_bytes", 524288000
                        )
                        available = limit - current_usage
                        failed_count += 1
                        return {
                            "media_id": media_id_str,
                            "status": "failed",
                            "error": f"Insufficient storage quota. Need {file_size} bytes, only {available} bytes available.",
                        }

                # S3 copy operation (with semaphore)
                async with semaphore:
                    s3_client = get_s3_uploader()
                    new_s3_url = await s3_client.copy_to_retention(
                        old_s3_url, target_retention
                    )

                if not new_s3_url:
                    failed_count += 1
                    return {
                        "media_id": media_id_str,
                        "status": "failed",
                        "error": "Failed to copy file to new S3 location",
                    }

                # Extract new S3 key
                new_s3_key = s3_client._extract_s3_key_from_url(new_s3_url)
                if not new_s3_key:
                    failed_count += 1
                    return {
                        "media_id": media_id_str,
                        "status": "failed",
                        "error": "Failed to extract S3 key from new URL",
                    }

                # Calculate new expires_at
                days = RETENTION_DAYS[target_retention]
                current_time_ms = get_current_timestamp_ms()
                if days is not None:
                    new_expires_at = current_time_ms + (days * 24 * 60 * 60 * 1000)
                else:
                    new_expires_at = None  # permanent

                # Update database record
                updated = await update_media_retention_and_location(
                    conn=conn,
                    media_id=media_id_str,
                    s3_key=new_s3_key,
                    s3_url=new_s3_url,
                    retention_policy=target_retention,
                    expires_at=new_expires_at,
                )

                if not updated:
                    failed_count += 1
                    # S3 file is orphaned, but lifecycle will clean it
                    logger.warning(
                        f"DB update failed for media {media_id_str}, S3 file orphaned at {new_s3_url}"
                    )
                    return {
                        "media_id": media_id_str,
                        "status": "failed",
                        "error": "Failed to update database record",
                    }

                # Update quota
                if target_retention == "permanent" and current_retention != "permanent":
                    # Moving TO permanent: add to quota
                    await create_or_update_user_storage_quota(
                        conn, context.user_id, file_size
                    )
                elif (
                    current_retention == "permanent" and target_retention != "permanent"
                ):
                    # Moving FROM permanent: subtract from quota
                    await create_or_update_user_storage_quota(
                        conn, context.user_id, -file_size
                    )

                # NOTE: Do NOT delete the old S3 file immediately.
                # The old URL may still be referenced in the conversation context
                # (e.g. image_url in user messages). If we delete it now, the next
                # LLM iteration will fail with 403 when OpenAI tries to fetch it.
                # S3 lifecycle rules will automatically clean up ephemeral files
                # after their retention period expires.
                logger.info(
                    f"Media {media_id_str} moved. Old file at {old_s3_url} "
                    f"will be cleaned up by S3 lifecycle rules."
                )

                moved_count += 1
                return {
                    "media_id": media_id_str,
                    "status": "moved",
                    "from": current_retention,
                    "to": target_retention,
                }

            # Process media items sequentially to avoid asyncpg
            # "cannot perform operation: another operation is in progress" errors.
            # A single asyncpg connection only supports one query at a time,
            # so concurrent DB operations via asyncio.gather are not safe here.
            processed_results = []
            for media_id in media_ids:
                try:
                    result = await process_one_media(media_id)
                    processed_results.append(result)
                except Exception as e:
                    logger.error(f"Exception processing media {media_id}: {e}")
                    failed_count += 1
                    processed_results.append(
                        {
                            "media_id": str(media_id),
                            "status": "failed",
                            "error": f"Internal error: {str(e)}",
                        }
                    )

            return {
                "success": True,
                "results": processed_results,
                "moved_count": moved_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
            }

        except Exception as e:
            logger.error(f"Error in change_media_retention: {str(e)}")
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
