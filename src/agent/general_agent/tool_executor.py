"""Slim tool executor - orchestration only."""

import json
import traceback
import re
from typing import Any, Dict, Optional

import asyncpg

from src.agent.tools.base import ToolCallContext
from src.agent.tools.registry import ToolRegistry
from src.agent.general_agent.utils.temp_message_accumulator import (
    TempMessageAccumulator,
)
from src.api.openai_conversations.schemas import MessageResponse
from src.socket_service import SocketService
from src.agent.general_agent.context.manager import AgentContextManager
from src.database.postgres.connection import async_db_savepoint
from src.utils.logger import get_logger

logger = get_logger()


def _sanitize_string_value(text: str) -> str:
    """Remove null bytes and problematic control characters from string."""
    if not isinstance(text, str):
        return text
    # Remove null bytes (PostgreSQL doesn't allow these)
    text = text.replace("\u0000", "")
    # Remove other problematic control characters (but keep common ones like \n, \t, \r)
    text = re.sub(r"[\u0000-\u0008\u000B-\u000C\u000E-\u001F]", "", text)
    return text


def _sanitize_arguments(args: Any) -> Any:
    """Recursively sanitize arguments to remove null characters."""
    if isinstance(args, dict):
        return {k: _sanitize_arguments(v) for k, v in args.items()}
    elif isinstance(args, list):
        return [_sanitize_arguments(item) for item in args]
    elif isinstance(args, str):
        return _sanitize_string_value(args)
    else:
        return args


def _create_error_output(
    conv_id: str,
    call_id: str,
    error_message: str,
    error_details: Dict[str, Any] = None,
) -> MessageResponse:
    """Create an error function_call_output message."""
    import uuid
    import time

    # Use structured error_details if provided, otherwise create simple error dict
    if error_details:
        error_output = {"success": False, **error_details}
    else:
        error_output = {"success": False, "error": error_message}

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
        function_output=error_output,
        status="completed",
        metadata=None,
        created_at=current_time,
        updated_at=current_time,
    )


class ToolExecutor:
    """Slim executor - orchestration only."""

    def __init__(
        self,
        socket_service: SocketService,
        context_manager: AgentContextManager,
        registry: ToolRegistry,
    ):
        self.socket_service = socket_service
        self.context_manager = context_manager
        self.registry = registry

    async def execute_tool_calls(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        response_dict: Dict[str, Any],
        accumulator: TempMessageAccumulator,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Execute all tool calls from response_dict."""
        output_items = response_dict.get("output", [])

        for item in output_items:
            if item.get("type") != "function_call":
                continue

            call_id = item.get("call_id")
            name = item.get("name")
            arguments_str = item.get("arguments", "{}")

            # Sanitize arguments_str to remove null character escape sequences
            # PostgreSQL doesn't support \u0000, so we remove them before parsing
            if isinstance(arguments_str, str):
                # Remove \u0000 escape sequences from JSON string
                arguments_str = arguments_str.replace("\\u0000", "")

            # Parse arguments
            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse arguments for tool {name}: {str(e)}")
                arguments = {}

            if not isinstance(arguments, dict):
                arguments = {}

            # Recursively sanitize arguments dict to remove null characters
            # This handles cases where null chars are already decoded from JSON
            arguments = _sanitize_arguments(arguments)

            # Get tool from registry
            tool = self.registry.get(name)
            if not tool:
                logger.error(f"Unknown tool: {name}")
                error_message = _create_error_output(
                    conv_id=conv_id,
                    call_id=call_id,
                    error_message=f"Unknown tool: {name}",
                )
                accumulator.store_message(error_message)
                error_msg_dict = error_message.model_dump(mode="json")
                await self.socket_service.emit_agent_event(
                    user_id=user_id,
                    conv_id=conv_id,
                    branch_id=branch_id,
                    agent_response_id=agent_resp_id,
                    msg_type="function_call_output",
                    event_name=None,
                    msg_item=error_msg_dict,
                    subagent_metadata=subagent_metadata,
                )
                continue

            # Create context
            context = ToolCallContext(
                user_id=user_id,
                conv_id=conv_id,
                branch_id=branch_id,
                agent_response_id=agent_resp_id,
                call_id=call_id,
                tool_name=name,
                arguments=arguments,
            )

            # Wrap each tool execution in a savepoint to isolate failures
            # This ensures one tool failure doesn't abort the entire transaction
            savepoint_name = f"tool_{name}_{call_id[:8]}"
            try:
                async with async_db_savepoint(conn, savepoint_name):
                    # 1. Execute tool
                    raw_result = await tool.execute(conn, context, arguments)

                    # 2. Process result
                    result = tool.process_result(context, raw_result)

                    # 3. Store messages in accumulator
                    accumulator.store_message(result.output_message)
                    if result.human_message:
                        accumulator.store_message(result.human_message)

                    # 4. Post-process (emit events, mark obsolete, etc.)
                    # Wrap post_process in try-catch to handle its errors separately
                    try:
                        await tool.post_process(
                            conn,
                            context,
                            result,
                            self.socket_service,
                            self.context_manager,
                            accumulator,
                            subagent_metadata,
                        )
                    except Exception as post_process_error:
                        # Log post_process error but don't fail the tool execution
                        logger.warning(
                            f"Tool {name} post_process failed (tool execution succeeded): {str(post_process_error)}"
                        )
                        logger.debug(
                            f"Post-process error traceback: {traceback.format_exc()}"
                        )

            except Exception as e:
                # Tool execution or savepoint operation failed
                # Log full error details for debugging
                error_traceback = traceback.format_exc()
                logger.error(
                    f"Tool {name} execution failed: {str(e)}\nTraceback:\n{error_traceback}"
                )

                # Create error output with structured error info
                error_details = {
                    "error": str(e),
                    "tool_name": name,
                    "error_type": type(e).__name__,
                }
                # Include more context for database errors
                if isinstance(e, asyncpg.PostgresError):
                    error_details["postgres_error_code"] = getattr(e, "sqlstate", None)
                    error_details["postgres_error_detail"] = getattr(e, "detail", None)

                error_message = _create_error_output(
                    conv_id=conv_id,
                    call_id=call_id,
                    error_message=str(e),
                    error_details=error_details,
                )
                accumulator.store_message(error_message)

                # Emit error event
                error_msg_dict = error_message.model_dump(mode="json")
                await self.socket_service.emit_agent_event(
                    user_id=user_id,
                    conv_id=conv_id,
                    branch_id=branch_id,
                    agent_response_id=agent_resp_id,
                    msg_type="function_call_output",
                    event_name=None,
                    msg_item=error_msg_dict,
                    subagent_metadata=subagent_metadata,
                )
