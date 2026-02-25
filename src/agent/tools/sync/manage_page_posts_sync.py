"""Manage page posts sync tool - sync posts or check status.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):

When action="sync":
   {
     "success": true,
     "action": "sync",
     "status": str,  # "completed", "partial", "error"
     "fan_page_id": str,
     "synced_posts": int,
     "has_more": bool,  # True if more posts available
     "cursor": str | None,  # Cursor for resuming sync
     "error": str | None  # Only present if status="error"
   }

When action="status":
   {
     "success": true,
     "action": "status",
     "fan_page_id": str,
     "posts_sync": {
       "status": str,  # "completed", "in_progress", "not_started", "error"
       "total_synced_posts": int,
       "cursor": str | None
     },
     "error": str | None  # Only present if status="error"
   }
"""

import uuid
import time
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.services.facebook.facebook_sync_job_manager import (
    FacebookSyncJobManager,
    SyncType,
    SyncMode,
)
from src.services.facebook.posts.post_sync_service import PostSyncService
from src.services.facebook.auth import FacebookPageService
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
Sync posts from Facebook or check sync status for a page.

WHEN TO USE:
- action="sync": Pull latest posts from Facebook into database
- action="sync": When local posts data seems outdated
- action="status": Check how many posts are synced, sync progress
- action="status": Before syncing to see if needed

PREREQUISITES:
- Requires page_id with valid Facebook access token

NEXT STEPS:
- After syncing posts, use manage_post_comments_sync to sync comments

RETURNS: 
- sync: Number of posts synced, cursor state, has_more flag
- status: Total synced posts, sync completion status
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


class ManagePagePostsSyncTool(BaseTool):
    """Tool to sync posts from Facebook or check sync status."""

    def __init__(
        self,
        sync_job_manager: FacebookSyncJobManager = None,
        post_sync_service: PostSyncService = None,
        page_service: FacebookPageService = None,
        page_scope_user_service: PageScopeUserService = None,
    ):
        # For job-based sync (preferred)
        self._sync_job_manager = sync_job_manager

        # For status checks (legacy)
        if post_sync_service:
            self._sync_service = post_sync_service
        else:
            page_svc = page_service or FacebookPageService()
            psus_svc = page_scope_user_service or PageScopeUserService()
            self._sync_service = PostSyncService(page_svc, psus_svc)

    @property
    def name(self) -> str:
        return "manage_page_posts_sync"

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
                        "description": "Action to perform: 'sync' to sync posts, 'status' to check sync status.",
                    },
                    "page_id": {
                        "type": "string",
                        "description": "Facebook page ID",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 25,
                        "description": "Maximum number of posts to sync in this batch (1-100). Only for action='sync'.",
                    },
                    "continue_from_cursor": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether to continue from saved cursor (resume previous sync). Only for action='sync'.",
                    },
                },
                "required": ["action", "page_id"],
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

        if not page_id:
            return {"success": False, "error": "page_id is required"}

        if not action:
            return {"success": False, "error": "action is required"}

        if action == "sync":
            if not self._sync_job_manager:
                return {
                    "success": False,
                    "error": "sync_job_manager_not_available",
                    "message": "FacebookSyncJobManager is not initialized",
                }

            limit = arguments.get("limit", 25)
            continue_from_cursor = arguments.get("continue_from_cursor", True)

            # Submit job via FacebookSyncJobManager in SYNC mode (wait for completion)
            result = await self._sync_job_manager.submit_sync(
                sync_type=SyncType.POSTS,
                payload={
                    "page_id": page_id,
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
            result = await self._sync_service.get_sync_status(
                conn=conn, page_id=page_id
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
