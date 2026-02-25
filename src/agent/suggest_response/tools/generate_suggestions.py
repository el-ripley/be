"""Generate suggestions tool - structured output tool for suggest_response_agent.

The agent provides media_ids (UUIDs) instead of raw URLs for image attachments.
The runner resolves media_ids → s3_urls before downstream delivery (FE, GraphAPI).

Media validation: Before accepting suggestions, the tool checks that every
referenced media_id is still alive (exists, owned by user, not expired).
If any media is dead/expired, execute() raises MediaValidationError so the
iteration loop returns an error to the agent and allows a retry.
"""

import time
import uuid
from typing import Any, Dict, List, Optional

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.utils.logger import get_logger

logger = get_logger()


class MediaValidationError(Exception):
    """Raised when media referenced in suggestions is expired or unavailable."""

    pass


def _build_messages_description(num_suggestions: int) -> str:
    return (
        f"Generate exactly {num_suggestions} message reply suggestions. "
        "Each suggestion should be a complete, ready-to-send message in PLAIN TEXT (no markdown — no **bold**, *italic*, or # headings). "
        "Provide variety across suggestions (different tones, approaches, or lengths). "
        "For image attachments, use media_ids from your context "
        "(<page_memory>/<user_memory> image tags or conversation attachment media_id). "
        "Do NOT fabricate media_ids — only use IDs that appear in your context."
    )


def _build_comments_description(num_suggestions: int) -> str:
    return (
        f"Generate exactly {num_suggestions} comment reply suggestions. "
        "Each suggestion should be a concise, ready-to-send comment reply in PLAIN TEXT — no markdown (public visibility). "
        "Provide variety across suggestions (different tones, approaches, or lengths). "
        "For image attachments, use attachment_media_id from your context "
        "(<page_memory> image tags or conversation attachment media_id). "
        "Do NOT fabricate media_ids — only use IDs that appear in your context."
    )


def build_generate_suggestions_definition(
    conversation_type: str, num_suggestions: int
) -> Dict[str, Any]:
    """Build OpenAI tool definition for generate_suggestions.

    Args:
        conversation_type: 'messages' or 'comments'
        num_suggestions: Number of suggestions to generate

    Returns:
        OpenAI tool definition dict
    """
    if conversation_type == "messages":
        description = _build_messages_description(num_suggestions)
        suggestion_schema = {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The suggested message text to send",
                },
                "media_ids": {
                    "anyOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "null"},
                    ],
                    "description": "Optional list of media asset UUIDs for image attachments. Use media_id values from <image> tags in page_memory/user_memory or from conversation attachment media_id. Set null if no images needed.",
                },
                "video_url": {
                    "type": ["string", "null"],
                    "description": "Optional video URL from context (must be a valid URL from your context, not fabricated). Set null if no video needed.",
                },
                "reply_to_ref": {
                    "type": ["string", "null"],
                    "description": "Message reference (e.g. '#5') to reply to as a threaded reply on Messenger. Must match a #N label from the conversation. Set null for a normal (non-threaded) reply.",
                },
            },
            "required": [
                "message",
                "media_ids",
                "video_url",
                "reply_to_ref",
            ],
            "additionalProperties": False,
        }
    else:
        description = _build_comments_description(num_suggestions)
        suggestion_schema = {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The suggested comment reply text",
                },
                "attachment_media_id": {
                    "type": ["string", "null"],
                    "description": "Optional media asset UUID for image attachment. Use media_id from <image> tags in page_memory or from conversation attachment media_id. Set null if no attachment needed.",
                },
            },
            "required": ["message", "attachment_media_id"],
            "additionalProperties": False,
        }

    return {
        "type": "function",
        "name": "generate_suggestions",
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "suggestions": {
                    "type": "array",
                    "items": suggestion_schema,
                    "minItems": num_suggestions,
                    "maxItems": num_suggestions,
                    "description": f"Array of exactly {num_suggestions} suggestions",
                }
            },
            "required": ["suggestions"],
            "additionalProperties": False,
        },
        "strict": True,
    }


def _create_function_call_output(
    conv_id: str, call_id: str, function_output: Any
) -> MessageResponse:
    """Build function_call_output MessageResponse."""
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


class GenerateSuggestionsTool(BaseTool):
    """Tool that accepts the LLM's suggestions payload and returns it as function_call_output.

    Definition is built per-request from conversation_type and num_suggestions.
    No DB or external call; execute() just validates and returns the arguments.
    """

    def __init__(
        self,
        conversation_type: str,
        num_suggestions: int,
    ) -> None:
        super().__init__(description_override=None)
        self._conversation_type = conversation_type
        self._num_suggestions = num_suggestions

    @property
    def name(self) -> str:
        return "generate_suggestions"

    @property
    def definition(self) -> Dict[str, Any]:
        return build_generate_suggestions_definition(
            self._conversation_type, self._num_suggestions
        )

    def _extract_media_ids(self, arguments: Dict[str, Any]) -> List[str]:
        """Extract all unique media_ids from suggestion arguments."""
        media_ids: set = set()
        for s in arguments.get("suggestions", []):
            if self._conversation_type == "messages":
                for mid in s.get("media_ids") or []:
                    if mid:
                        media_ids.add(mid)
            else:
                mid = s.get("attachment_media_id")
                if mid:
                    media_ids.add(mid)
        return list(media_ids)

    async def _validate_media_liveness(
        self, user_id: str, media_ids: List[str]
    ) -> None:
        """Check that all referenced media assets are actually alive on S3.

        Two-phase validation:
        Phase 1 (DB) — fast sanity checks:
          - Media exists and belongs to the user
          - Media status is not 'failed'
          - Media has a non-empty s3_url
        Phase 2 (S3 HEAD) — real check:
          - Batch HEAD requests to verify files actually exist on S3
          - Catches cases where S3 lifecycle already deleted the file

        Raises:
            MediaValidationError: With details of dead media if any are found.
        """
        from src.database.postgres.connection import async_db_transaction
        from src.database.postgres.repositories.media_assets_queries import (
            get_media_assets_by_ids,
        )
        from src.common.s3_client import get_s3_uploader

        dead_media: List[str] = []

        # --- Phase 1: DB sanity checks ---
        async with async_db_transaction() as conn:
            assets = await get_media_assets_by_ids(conn, media_ids, user_id)

        found_map = {str(a["id"]): a for a in assets}

        # Collect s3_urls for media that pass DB checks → will verify on S3
        urls_to_check: Dict[str, str] = {}  # media_id → s3_url

        for mid in media_ids:
            asset = found_map.get(mid)
            if not asset:
                dead_media.append(
                    f"  - media_id={mid}: not found or does not belong to you"
                )
                continue

            if asset.get("status") == "failed":
                dead_media.append(f"  - media_id={mid}: upload failed")
                continue

            s3_url = asset.get("s3_url")
            if not s3_url:
                dead_media.append(f"  - media_id={mid}: no S3 URL available")
                continue

            urls_to_check[mid] = s3_url

        # --- Phase 2: S3 HEAD check for surviving media ---
        if urls_to_check:
            s3_client = get_s3_uploader()
            unique_urls = list(set(urls_to_check.values()))
            s3_results = await s3_client.batch_check_files_exist(unique_urls)

            for mid, url in urls_to_check.items():
                if not s3_results.get(url, False):
                    retention = found_map[mid].get("retention_policy", "unknown")
                    dead_media.append(
                        f"  - media_id={mid}: file no longer exists on storage "
                        f"(retention_policy={retention})"
                    )

        if dead_media:
            details = "\n".join(dead_media)
            raise MediaValidationError(
                f"MEDIA VALIDATION FAILED — the following media are expired/unavailable:\n"
                f"{details}\n\n"
                f"Please call generate_suggestions again WITHOUT the dead media_ids listed above."
            )

    async def execute(
        self,
        conn: Optional[Any],
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Validate media liveness, then return arguments as raw result.

        If any referenced media_id is dead/expired, raises MediaValidationError
        so the tool executor returns an error to the agent for retry.
        """
        media_ids = self._extract_media_ids(arguments)
        if media_ids:
            logger.debug(
                "generate_suggestions: validating %d media_id(s): %s",
                len(media_ids),
                media_ids,
            )
            await self._validate_media_liveness(context.user_id, media_ids)
        return arguments

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Build function_call_output message with suggestions payload."""
        output_message = _create_function_call_output(
            conv_id=context.conv_id,
            call_id=context.call_id,
            function_output=raw_result if isinstance(raw_result, dict) else {},
        )
        return ToolResult(
            output_message=output_message,
            human_message=None,
            metadata=None,
        )
