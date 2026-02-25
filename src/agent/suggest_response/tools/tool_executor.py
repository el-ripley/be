"""Tool executor for suggest_response_agent with SR DB connections and socket events."""

import json
import re
import uuid
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.agent.tools.base import ToolCallContext

if TYPE_CHECKING:
    from src.services.notifications.escalation_trigger import (
        EscalationNotificationTrigger,
    )
from src.agent.suggest_response.tools.tool_registry import SuggestResponseToolRegistry
from src.agent.suggest_response.socket.emitter import SuggestResponseSocketEmitter
from src.agent.suggest_response.utils.message_accumulator import (
    SuggestResponseMessageAccumulator,
)
from src.api.openai_conversations.schemas import MessageResponse
from src.utils.logger import get_logger

logger = get_logger()

# Terminal tools - failure triggers retry instead of stopping
TERMINAL_TOOLS = frozenset({"generate_suggestions", "complete_task"})


def _sanitize_string_value(text: str) -> str:
    """Remove null bytes and problematic control characters from string."""
    if not isinstance(text, str):
        return text
    text = text.replace("\u0000", "")
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
) -> MessageResponse:
    """Create an error function_call_output message."""
    output_uuid = str(uuid.uuid4())
    current_time = int(time.time() * 1000)
    error_output = {"success": False, "error": error_message}
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


class SuggestResponseToolExecutor:
    """Execute tools for suggest_response_agent with SR connections and socket events."""

    def __init__(
        self,
        registry: SuggestResponseToolRegistry,
        socket_emitter: SuggestResponseSocketEmitter,
        escalation_trigger: Optional["EscalationNotificationTrigger"] = None,
    ) -> None:
        self.registry = registry
        self.socket_emitter = socket_emitter
        self.escalation_trigger = escalation_trigger

    async def execute_tool_calls(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        page_scope_user_id: Optional[str],
        run_id: str,
        iteration_index: int,
        response_dict: Dict[str, Any],
        accumulator: SuggestResponseMessageAccumulator,
        num_suggestions: int,
        step: Optional[str] = None,
    ) -> bool:
        """Execute tool calls from response_dict; insert each output right after its call.
        Emits tool_result with full msg_item (function_call_output message as dict).

        Returns:
            True if a terminal tool (generate_suggestions, complete_task) failed,
            signalling the iteration loop to allow a retry instead of stopping.
        """
        output_items = response_dict.get("output", [])
        num_inserted = 0
        terminal_tool_failed = False

        for idx, item in enumerate(output_items):
            if item.get("type") != "function_call":
                continue

            call_id = item.get("call_id")
            name = item.get("name")
            arguments_str = item.get("arguments", "{}")

            if isinstance(arguments_str, str):
                arguments_str = arguments_str.replace("\\u0000", "")

            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            arguments = _sanitize_arguments(arguments)

            tool = self.registry.get(name, conversation_type, num_suggestions)
            position_in_order = idx + num_inserted

            if not tool:
                error_msg = _create_error_output(
                    conversation_id, call_id, f"Unknown tool: {name}"
                )
                accumulator.insert_after_position(position_in_order, error_msg)
                num_inserted += 1
                msg_item = error_msg.model_dump(mode="json")
                await self.socket_emitter.emit_tool_result(
                    user_id=user_id,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    iteration_index=iteration_index,
                    msg_item=msg_item,
                    step=step,
                )
                continue

            context = ToolCallContext(
                user_id=user_id,
                conv_id=conversation_id,
                branch_id="",
                agent_response_id="",
                call_id=call_id,
                tool_name=name,
                arguments=arguments,
                fb_conversation_type=conversation_type,
                fb_conversation_id=conversation_id,
                fan_page_id=fan_page_id,
                page_scope_user_id=page_scope_user_id,
            )

            try:
                raw_result = await tool.execute(None, context, arguments)
                result = tool.process_result(context, raw_result)
                accumulator.insert_after_position(
                    position_in_order, result.output_message
                )
                num_inserted += 1
                msg_item = result.output_message.model_dump(mode="json")
                await self.socket_emitter.emit_tool_result(
                    user_id=user_id,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    iteration_index=iteration_index,
                    msg_item=msg_item,
                    step=step,
                )
                if (
                    self.escalation_trigger
                    and name == "sql_query"
                    and arguments.get("mode") == "write"
                    and isinstance(raw_result, dict)
                    and raw_result.get("success")
                ):
                    try:
                        await self.escalation_trigger.check_and_notify(
                            user_id=user_id,
                            conversation_type=conversation_type,
                            conversation_id=conversation_id,
                            fan_page_id=fan_page_id,
                            sql_statements=arguments.get("sqls", []),
                            raw_result=raw_result,
                        )
                    except Exception:
                        logger.warning(
                            "Escalation notification trigger failed",
                            exc_info=True,
                        )

            except Exception as e:
                if name in TERMINAL_TOOLS:
                    terminal_tool_failed = True
                    logger.warning("%s failed (will retry): %s", name, e)
                else:
                    logger.error(f"Tool {name} execution failed: {e}", exc_info=True)
                error_msg = _create_error_output(conversation_id, call_id, str(e))
                accumulator.insert_after_position(position_in_order, error_msg)
                num_inserted += 1
                msg_item = error_msg.model_dump(mode="json")
                await self.socket_emitter.emit_tool_result(
                    user_id=user_id,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    iteration_index=iteration_index,
                    msg_item=msg_item,
                    step=step,
                )

        return terminal_tool_failed
