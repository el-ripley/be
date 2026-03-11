"""Manage post comments sync tool - sync comment threads or check status.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):

When action="sync":
   {
     "success": true,
     "action": "sync",
     "status": str,  # "completed", "partial", "error"
     "post_id": str,
     "fan_page_id": str,
     "synced_root_comments": int,  # Number of root comment threads synced
     "synced_total_comments": int,  # Total comments (including replies)
     "has_more": bool,  # True if more comment threads available
     "cursor": str | None,  # Cursor for resuming sync
     "error": str | None  # Only present if status="error"
   }

When action="status":
   {
     "success": true,
     "action": "status",
     "post_id": str,
     "status": str,  # "completed", "in_progress", "not_started", "error"
     "total_synced_root_comments": int,
     "total_synced_comments": int,
     "error": str | None  # Only present if status="error"
   }
"""

import time
import uuid
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.services.facebook.auth import FacebookPageService
from src.services.facebook.comments.comment_conversation_service import (
    CommentConversationService,
)
from src.services.facebook.comments.sync.comment_sync_service import CommentSyncService
from src.services.facebook.facebook_sync_job_manager import (
    FacebookSyncJobManager,
    SyncMode,
    SyncType,
)
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
Sync comment threads from Facebook or check sync status for a post.

WHEN TO USE:
- action="sync": Pull latest comments for a specific post
- action="sync": When comment data seems outdated
- action="status": Check how many comments are synced for a post
- action="status": Before syncing to see current progress

PREREQUISITES:
- Requires page_id and post_id
- Post should exist (use manage_page_posts_sync first if needed)

RETURNS:
- sync: Root comments synced, total comments synced, cursor state
- status: Total synced comments, sync completion status
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


class ManagePostCommentsSyncTool(BaseTool):
    """Tool to sync comment threads or check sync status."""

    def __init__(
        self,
        sync_job_manager: FacebookSyncJobManager = None,
        comment_sync_service: CommentSyncService = None,
        page_service: FacebookPageService = None,
        page_scope_user_service: PageScopeUserService = None,
    ):
        # For job-based sync (preferred)
        self._sync_job_manager = sync_job_manager

        # For status checks (legacy)
        if comment_sync_service:
            self._sync_service = comment_sync_service
        else:
            page_svc = page_service or FacebookPageService()
            psus_svc = page_scope_user_service or PageScopeUserService()
            conv_svc = CommentConversationService()
            self._sync_service = CommentSyncService(page_svc, psus_svc, conv_svc)

    @property
    def name(self) -> str:
        return "manage_post_comments_sync"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["sync", "status"],
                        "description": "Action to perform: 'sync' to sync comments, 'status' to check sync status.",
                    },
                    "page_id": {
                        "type": "string",
                        "description": "Facebook page ID",
                    },
                    "post_id": {
                        "type": "string",
                        "description": "Facebook post ID",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 10,
                        "description": "Maximum number of root comment trees to sync per batch (1-50). Only for action='sync'.",
                    },
                    "continue_from_cursor": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether to continue from saved cursor (resume previous sync). Only for action='sync'.",
                    },
                },
                "required": ["action", "post_id"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - sync or check status."""
        action = arguments.get("action")
        page_id = arguments.get("page_id")
        post_id = arguments.get("post_id")

        if not post_id:
            return {"success": False, "error": "post_id is required"}

        if not action:
            return {"success": False, "error": "action is required"}

        if action == "sync":
            if not page_id:
                return {
                    "success": False,
                    "error": "page_id is required for sync action",
                }

            if not self._sync_job_manager:
                return {
                    "success": False,
                    "error": "sync_job_manager_not_available",
                    "message": "FacebookSyncJobManager is not initialized",
                }

            limit = arguments.get("limit", 10)
            continue_from_cursor = arguments.get("continue_from_cursor", True)

            # Submit job via FacebookSyncJobManager in SYNC mode (wait for completion)
            result = await self._sync_job_manager.submit_sync(
                sync_type=SyncType.COMMENTS,
                payload={
                    "page_id": page_id,
                    "post_id": post_id,
                    "limit": limit,
                    "continue_from_cursor": continue_from_cursor,
                },
                user_id=context.user_id,
                mode=SyncMode.SYNC,  # Wait for result
                timeout_seconds=300,  # 5 minutes
            )

            if not result["success"]:
                return {
                    "success": False,
                    "action": "sync",
                    "error": result.get("error"),
                    "message": result.get("message", "Sync failed"),
                }

            # Extract actual sync result from job result
            job_result = result.get("result", {})
            return {
                "success": True,
                "action": "sync",
                "job_id": result.get("job_id"),
                **job_result,
            }

        elif action == "status":
            result = await self._sync_service.get_post_comment_sync_status(
                conn=conn, post_id=post_id
            )

            return {
                "success": True,
                "action": "status",
                **result,
            }

        else:
            return {"success": False, "error": "action must be 'sync' or 'status'"}

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
