"""Get page scope user tool - get information about a Facebook user.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):

When fetch="summary" (default):
   {
     "success": true,
     "psid": str,
     "page_id": str,
     "user_info": {
       "name": str | None,
       "profile_pic": str | None
     },
     "first_seen_at": int | None,  # timestamp
     "last_seen_at": int | None,    # timestamp
     "summary": {
       "total_comments": int,
       "total_post_reactions": int,
       "total_comment_reactions": int,
       "recent_comments": [
         {
           "comment_id": str,
           "post_id": str,
           "conversation_id": str | None,  # for context exploration
           "message": str | None,
           "created_at": int | None,
           "is_reply": bool
         }
         // ... up to 5 recent
       ],
       "recent_post_reactions": [
         {
           "reaction_type": str,  # LIKE, LOVE, HAHA, etc.
           "created_at": int,
           "post_id": str
         }
         // ... up to 5 recent
       ],
       "recent_comment_reactions": [
         {
           "reaction_type": str,
           "created_at": int,
           "post_id": str,
           "comment_id": str
         }
         // ... up to 5 recent
       ]
     }
   }

When fetch="comments":
   {
     "success": true,
     "psid": str,
     "page_id": str,
     "user_info": {...},
     "first_seen_at": int | None,
     "last_seen_at": int | None,
     "comments": {
       "items": [...],  # same structure as recent_comments above
       "total": int,
       "limit": int,
       "offset": int,
       "has_more": bool
     }
   }

When fetch="post_reactions" or "comment_reactions":
   {
     "success": true,
     "psid": str,
     "page_id": str,
     "user_info": {...},
     "first_seen_at": int | None,
     "last_seen_at": int | None,
     "post_reactions": {  # or "comment_reactions"
       "items": [...],  # same structure as recent_*_reactions above
       "total": int,
       "limit": int,
       "offset": int,
       "has_more": bool
     }
   }
"""

import uuid
import time
import json
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.database.postgres.repositories.facebook_queries.user_interactions import (
    count_user_comments,
    count_user_post_reactions,
    count_user_comment_reactions,
    get_user_comments_minimal,
    get_user_post_reactions_minimal,
    get_user_comment_reactions_minimal,
)
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
Get information about a Facebook user (PSID) with smart layered discovery.

WHEN TO USE:
- Look up user profile and interaction summary
- Discover user's comments, reactions with context references
- Drill down into specific interaction categories

HOW IT WORKS:
1. DEFAULT (fetch="summary"): Returns user info + counts + recent 5 items of each type
2. DRILL DOWN: Use fetch="comments|post_reactions|comment_reactions" with pagination

PREREQUISITES:
- Requires psid

RETURNS: 
- Summary: User info + counts + recent samples with context references (post_id, conversation_id)
- Detail: Paginated full list when fetch is specified
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


class GetPageScopeUserTool(BaseTool):
    """Tool to get information about a page-scoped user."""

    def __init__(
        self,
        page_scope_user_service: PageScopeUserService = None,
    ):
        self._page_scope_user_service = (
            page_scope_user_service or PageScopeUserService()
        )

    @property
    def name(self) -> str:
        return "get_page_scope_user"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "psid": {
                        "type": "string",
                        "description": "Page-scoped user ID (PSID)",
                    },
                    "fetch": {
                        "type": "string",
                        "enum": [
                            "summary",
                            "comments",
                            "post_reactions",
                            "comment_reactions",
                        ],
                        "default": "summary",
                        "description": "What to fetch: 'summary' (default - counts + recent 5), 'comments', 'post_reactions', or 'comment_reactions' (paginated full details)",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                        "description": "Items per page when fetching details (comments/reactions). Ignored for 'summary'.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "Offset for pagination when fetching details. Ignored for 'summary'.",
                    },
                },
                "required": ["psid"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - get page scope user with smart layered discovery."""
        psid = arguments.get("psid")
        fetch = arguments.get("fetch", "summary")
        limit = arguments.get("limit", 20)
        offset = arguments.get("offset", 0)

        if not psid:
            return {"success": False, "error": "psid are required"}

        # Get basic user info
        user = await self._page_scope_user_service.get_page_scope_user(conn, psid)
        page_id = user.get("fan_page_id")

        if not user:
            return {
                "success": False,
                "error": f"User {psid} not found",
            }

        user_info_raw = user.get("user_info") or {}
        if isinstance(user_info_raw, str):
            try:
                user_info = json.loads(user_info_raw)
            except (json.JSONDecodeError, TypeError):
                user_info = {}
        else:
            user_info = user_info_raw or {}

        # Base result
        result = {
            "success": True,
            "psid": psid,
            "page_id": page_id,
            "user_info": {
                "name": user_info.get("name"),
                "profile_pic": user_info.get("profile_pic"),
            },
            "first_seen_at": user.get("created_at"),
            "last_seen_at": user.get("updated_at"),
        }

        # Handle different fetch modes
        if fetch == "summary":
            # Get counts
            total_comments = await count_user_comments(conn, psid, page_id)
            total_post_reactions = await count_user_post_reactions(conn, psid, page_id)
            total_comment_reactions = await count_user_comment_reactions(
                conn, psid, page_id
            )

            # Get recent 5 of each type
            recent_comments, _ = await get_user_comments_minimal(
                conn, psid, page_id, limit=5, offset=0
            )
            recent_post_reactions, _ = await get_user_post_reactions_minimal(
                conn, psid, page_id, limit=5, offset=0
            )
            recent_comment_reactions, _ = await get_user_comment_reactions_minimal(
                conn, psid, page_id, limit=5, offset=0
            )

            result["summary"] = {
                "total_comments": total_comments,
                "total_post_reactions": total_post_reactions,
                "total_comment_reactions": total_comment_reactions,
                "recent_comments": recent_comments,
                "recent_post_reactions": recent_post_reactions,
                "recent_comment_reactions": recent_comment_reactions,
            }

        elif fetch == "comments":
            comments, total = await get_user_comments_minimal(
                conn, psid, page_id, limit, offset
            )
            result["comments"] = {
                "items": comments,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + len(comments)) < total,
            }

        elif fetch == "post_reactions":
            reactions, total = await get_user_post_reactions_minimal(
                conn, psid, page_id, limit, offset
            )
            result["post_reactions"] = {
                "items": reactions,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + len(reactions)) < total,
            }

        elif fetch == "comment_reactions":
            reactions, total = await get_user_comment_reactions_minimal(
                conn, psid, page_id, limit, offset
            )
            result["comment_reactions"] = {
                "items": reactions,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + len(reactions)) < total,
            }

        return result

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
