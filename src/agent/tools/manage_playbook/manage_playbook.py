"""Manage Playbook Tool — create, update, delete, search playbooks with Qdrant."""

from typing import Any, Dict, Optional

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.agent.tools.manage_playbook.tool_description import TOOL_DESCRIPTION
from src.agent.tools.sql_query.formatters import create_function_call_output
from src.database.postgres.connection import async_db_transaction
from src.services.playbook.playbook_sync_service import (
    create_playbook,
    delete_playbook,
    search_playbooks,
    update_playbook,
)
from src.utils.logger import get_logger

logger = get_logger()


class ManagePlaybookTool(BaseTool):
    """Tool to manage playbooks (create, update, delete, search) with Qdrant sync."""

    @property
    def name(self) -> str:
        return "manage_playbook"

    @property
    def definition(self) -> Dict[str, Any]:
        base_def = {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["create", "update", "delete", "search"],
                        "description": "Operation mode.",
                    },
                    "playbook_id": {
                        "type": ["string", "null"],
                        "description": "Playbook UUID. Required for update/delete. Null for create/search.",
                    },
                    "title": {
                        "type": ["string", "null"],
                        "description": "Playbook title. Required for create. Optional for update. Null for delete/search.",
                    },
                    "situation": {
                        "type": ["string", "null"],
                        "description": "When to apply this playbook. Required for create. Optional for update. Null for delete/search.",
                    },
                    "content": {
                        "type": ["string", "null"],
                        "description": "Guidance content. Required for create. Optional for update. Null for delete/search.",
                    },
                    "tags": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                        "description": "Categorization tags. Optional for create/update. Null for delete/search.",
                    },
                    "query": {
                        "type": ["string", "null"],
                        "description": "Search query text. Required for search. Null for create/update/delete.",
                    },
                    "limit": {
                        "type": ["integer", "null"],
                        "description": "Max search results (default 3, max 10). Only for search.",
                    },
                    "playbook_ids": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                        "description": "Filter search to these playbook UUIDs only. Only for search.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of what you're doing and why (for audit log).",
                    },
                },
                "required": [
                    "mode",
                    "playbook_id",
                    "title",
                    "situation",
                    "content",
                    "tags",
                    "query",
                    "limit",
                    "playbook_ids",
                    "description",
                ],
                "additionalProperties": False,
            },
            "strict": True,
        }
        return self._apply_description_override(base_def)

    def _validate_and_extract(
        self, arguments: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Validate arguments; return error dict if invalid."""
        mode = arguments.get("mode")
        if not mode or mode not in ("create", "update", "delete", "search"):
            return {
                "success": False,
                "error": "mode must be create, update, delete, or search",
                "error_type": "ValueError",
            }

        if mode == "create":
            if not arguments.get("title"):
                return {
                    "success": False,
                    "error": "title is required for create",
                    "error_type": "ValueError",
                }
            if not arguments.get("situation"):
                return {
                    "success": False,
                    "error": "situation is required for create",
                    "error_type": "ValueError",
                }
            if not arguments.get("content"):
                return {
                    "success": False,
                    "error": "content is required for create",
                    "error_type": "ValueError",
                }
        elif mode == "update":
            pid = arguments.get("playbook_id")
            if not pid:
                return {
                    "success": False,
                    "error": "playbook_id is required for update",
                    "error_type": "ValueError",
                }
        elif mode == "delete":
            pid = arguments.get("playbook_id")
            if not pid:
                return {
                    "success": False,
                    "error": "playbook_id is required for delete",
                    "error_type": "ValueError",
                }
        elif mode == "search":
            if not arguments.get("query"):
                return {
                    "success": False,
                    "error": "query is required for search",
                    "error_type": "ValueError",
                }

        return None

    async def execute(
        self,
        conn: Optional[asyncpg.Connection],
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        err = self._validate_and_extract(arguments)
        if err:
            return err

        mode = arguments["mode"]

        # Permission guard: suggest_response context can only search
        if context.fb_conversation_type is not None and mode != "search":
            return {
                "success": False,
                "error": "Only search mode is allowed in suggest_response context",
                "error_type": "PermissionError",
            }

        try:
            if mode == "create":
                return await self._execute_create(conn, context, arguments)

            if mode == "update":
                return await self._execute_update(conn, context, arguments)

            if mode == "delete":
                return await self._execute_delete(conn, context, arguments)

            if mode == "search":
                return await self._execute_search(conn, context, arguments)

        except ValueError as e:
            return {"success": False, "error": str(e), "error_type": "ValueError"}
        except Exception as e:
            logger.error(f"manage_playbook error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }

        return {
            "success": False,
            "error": f"Unknown mode: {mode}",
            "error_type": "ValueError",
        }

    async def _execute_create(
        self,
        conn: Optional[asyncpg.Connection],
        context: ToolCallContext,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Use system connection; agent conn is for sql_query only
        async with async_db_transaction() as tx_conn:
            playbook_id = await create_playbook(
                conn=tx_conn,
                title=args["title"],
                situation=args["situation"],
                content=args["content"],
                tags=args.get("tags"),
                user_id=context.user_id,
                agent_response_id=context.agent_response_id or "",
                conversation_id=context.conv_id or None,
                branch_id=context.branch_id or None,
            )
            return {"success": True, "playbook_id": playbook_id, "title": args["title"]}

    async def _execute_update(
        self,
        conn: Optional[asyncpg.Connection],
        context: ToolCallContext,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Use system connection; agent conn is for sql_query only
        async with async_db_transaction() as tx_conn:
            updated = await update_playbook(
                conn=tx_conn,
                playbook_id=args["playbook_id"],
                user_id=context.user_id,
                agent_response_id=context.agent_response_id or "",
                title=args.get("title"),
                situation=args.get("situation"),
                content=args.get("content"),
                tags=args.get("tags"),
                conversation_id=context.conv_id or None,
                branch_id=context.branch_id or None,
            )
            return {
                "success": True,
                "playbook_id": args["playbook_id"],
                "updated_fields": updated,
            }

    async def _execute_delete(
        self,
        conn: Optional[asyncpg.Connection],
        context: ToolCallContext,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Use system connection; agent conn is for sql_query only
        async with async_db_transaction() as tx_conn:
            await delete_playbook(conn=tx_conn, playbook_id=args["playbook_id"])
            return {"success": True, "playbook_id": args["playbook_id"]}

    async def _execute_search(
        self,
        conn: Optional[asyncpg.Connection],
        context: ToolCallContext,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        # For suggest_response (conn is None), use main transaction for embedding logging
        search_limit = args.get("limit")
        if search_limit is None:
            search_limit = 3
        search_limit = min(
            max(1, int(search_limit) if isinstance(search_limit, int) else 3), 10
        )

        playbook_ids = args.get("playbook_ids")
        if playbook_ids is not None and not isinstance(playbook_ids, list):
            playbook_ids = None

        # Use main transaction for embedding logging (openai_response INSERT)
        # agent_reader/suggest_response_reader lack INSERT on openai_response
        async with async_db_transaction() as tx_conn:
            results = await search_playbooks(
                conn=tx_conn,
                query_text=args["query"],
                user_id=context.user_id,
                agent_response_id=context.agent_response_id or "",
                playbook_ids=playbook_ids,
                limit=search_limit,
                conversation_id=context.conv_id if conn else None,
                branch_id=context.branch_id if conn else None,
            )

        return {
            "success": True,
            "results": results,
            "result_count": len(results),
        }

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        output_message = create_function_call_output(
            conv_id=context.conv_id,
            call_id=context.call_id,
            function_output=(
                raw_result
                if isinstance(raw_result, dict)
                else {
                    "success": False,
                    "error": str(raw_result),
                    "error_type": "Unknown",
                }
            ),
        )
        return ToolResult(
            output_message=output_message, human_message=None, metadata=None
        )
