"""Iteration runner for suggest_response_agent - single LLM iteration with tool execution."""

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

from src.agent.general_agent.context.function_output_normalizer import (
    normalize_function_output_to_api_format,
)
from src.agent.suggest_response.socket.stream_handler import (
    SuggestResponseStreamHandler,
)
from src.agent.suggest_response.tools.tool_executor import SuggestResponseToolExecutor
from src.agent.suggest_response.utils.iteration_warning import (
    SuggestResponseIterationWarningInjector,
)
from src.agent.suggest_response.utils.message_accumulator import (
    SuggestResponseMessageAccumulator,
)
from src.api.openai_conversations.schemas import MessageResponse
from src.redis_client.redis_agent_manager import RedisAgentManager
from src.utils.logger import get_logger

logger = get_logger()

# Terminal tools - either one ends the iteration loop when executed successfully
TERMINAL_TOOLS = frozenset({"generate_suggestions", "complete_task"})


@dataclass
class SuggestResponseIterationResult:
    """Result of a single suggest_response iteration."""

    should_stop: bool
    is_final: bool
    reason: Literal["completed", "error", "continue"]
    response_dict: Optional[Dict[str, Any]] = None


def _reorder_output_terminal_tools_last(
    response_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """Reorder output so that terminal tool calls (generate_suggestions, complete_task) come last.
    Other items and relative order of non-terminal tools are preserved.
    """
    output_items = response_dict.get("output", [])
    result: List[Dict[str, Any]] = []
    pending_fc: List[Dict[str, Any]] = []

    def _is_terminal(name: str) -> bool:
        return name in TERMINAL_TOOLS

    for item in output_items:
        if item.get("type") == "function_call":
            pending_fc.append(item)
        else:
            if pending_fc:
                pending_fc.sort(key=lambda x: _is_terminal(x.get("name", "")))
                result.extend(pending_fc)
                pending_fc = []
            result.append(item)

    if pending_fc:
        pending_fc.sort(key=lambda x: _is_terminal(x.get("name", "")))
        result.extend(pending_fc)

    return {**response_dict, "output": result}


def _add_response_to_accumulator(
    accumulator: SuggestResponseMessageAccumulator,
    response_dict: Dict[str, Any],
    conv_id: str,
) -> None:
    """Add LLM response output items to an existing accumulator."""
    output_items = response_dict.get("output", [])
    current_time = int(time.time() * 1000)

    for item in output_items:
        item_id = item.get("id", str(uuid.uuid4()))
        item_type = item.get("type")
        msg_uuid = str(uuid.uuid4())
        accumulator.set_message_uuid(item_id, msg_uuid)
        accumulator.set_message_type(item_id, item_type or "message")

        if item_type == "function_call":
            call_id = item.get("call_id")
            name = item.get("name")
            arguments_str = item.get("arguments", "{}")
            try:
                args_dict = json.loads(arguments_str)
            except json.JSONDecodeError:
                args_dict = {}
            message = MessageResponse(
                id=msg_uuid,
                conversation_id=conv_id,
                sequence_number=0,
                type="function_call",
                role="assistant",
                content=None,
                call_id=call_id,
                function_name=name,
                function_arguments=args_dict,
                status="completed",
                metadata=None,
                created_at=current_time,
                updated_at=current_time,
            )
            accumulator.store_message(message)

        elif item_type == "message":
            content = item.get("content", [])
            status = item.get("status", "completed")
            message = MessageResponse(
                id=msg_uuid,
                conversation_id=conv_id,
                sequence_number=0,
                type="message",
                role="assistant",
                content=content,
                status=status,
                metadata={"source": "assistant"},
                created_at=current_time,
                updated_at=current_time,
            )
            accumulator.store_message(message)

        elif item_type == "reasoning":
            reasoning_summary = item.get("reasoning_summary") or item.get("summary")
            content = item.get("content")
            has_content = bool(
                (
                    reasoning_summary is not None
                    and reasoning_summary != ""
                    and reasoning_summary != []
                )
                or (content is not None and content != "" and content != [])
            )
            if not has_content:
                continue
            message = MessageResponse(
                id=msg_uuid,
                conversation_id=conv_id,
                sequence_number=0,
                type="reasoning",
                role="assistant",
                content=content,
                reasoning_summary=reasoning_summary,
                status="completed",
                metadata=None,
                created_at=current_time,
                updated_at=current_time,
            )
            accumulator.store_message(message)


def _convert_to_openai_entries(
    temp_messages: List[MessageResponse],
) -> List[Tuple[str, Dict[str, Any]]]:
    """Convert MessageResponse list to (msg_id, payload) for Redis append."""
    entries: List[Tuple[str, Dict[str, Any]]] = []
    for msg in temp_messages:
        message_dict: Dict[str, Any] = {"type": msg.type}
        if msg.type == "reasoning":
            message_dict["summary"] = msg.reasoning_summary or []
        elif msg.type == "function_call":
            message_dict["call_id"] = msg.call_id
            message_dict["name"] = msg.function_name
            message_dict["arguments"] = (
                json.dumps(msg.function_arguments) if msg.function_arguments else "{}"
            )
        elif msg.type == "function_call_output":
            message_dict["call_id"] = msg.call_id
            if isinstance(msg.function_output, list):
                message_dict["output"] = normalize_function_output_to_api_format(
                    msg.function_output
                )
            else:
                message_dict["output"] = (
                    json.dumps(msg.function_output) if msg.function_output else "{}"
                )
        elif msg.type == "message":
            message_dict["role"] = msg.role
            message_dict["content"] = msg.content or ""
        entries.append((msg.id, message_dict))
    return entries


def _is_terminal_tool_only(response_dict: Dict[str, Any]) -> bool:
    """True if response has only terminal tool call(s) (generate_suggestions or complete_task)."""
    output_items = response_dict.get("output", [])
    for item in output_items:
        if item.get("type") == "function_call":
            if item.get("name") not in TERMINAL_TOOLS:
                return False
    return True


def _has_terminal_tool(response_dict: Dict[str, Any]) -> bool:
    """True if response contains any terminal tool call (generate_suggestions or complete_task)."""
    output_items = response_dict.get("output", [])
    for item in output_items:
        if item.get("type") == "function_call" and item.get("name") in TERMINAL_TOOLS:
            return True
    return False


def _has_tool_calls(response_dict: Dict[str, Any]) -> bool:
    """True if response has any function_call."""
    output_items = response_dict.get("output", [])
    for item in output_items:
        if item.get("type") == "function_call":
            return True
    return False


class SuggestResponseIterationRunner:
    """Execute single LLM iteration for suggest_response_agent."""

    def __init__(
        self,
        stream_handler: SuggestResponseStreamHandler,
        tool_executor: SuggestResponseToolExecutor,
        redis_manager: RedisAgentManager,
    ) -> None:
        self.stream_handler = stream_handler
        self.tool_executor = tool_executor
        self.redis_manager = redis_manager

    async def run(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        page_scope_user_id: Optional[str],
        run_id: str,
        agent_response_id: str,
        temp_context: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        llm_call: Any,
        model: str,
        reasoning_param: Optional[Dict[str, Any]],
        current_iteration: int,
        max_iteration: int,
        accumulator: SuggestResponseMessageAccumulator,
        num_suggestions: int,
        verbosity: str = "low",
        step: Optional[str] = None,
    ) -> SuggestResponseIterationResult:
        """Execute a single iteration and optionally execute tools."""
        try:
            stream_result = await self.stream_handler.stream(
                llm_call=llm_call,
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                run_id=run_id,
                iteration_index=current_iteration,
                input_messages=temp_context,
                tools=tools,
                model=model,
                reasoning_param=reasoning_param,
                verbosity=verbosity,
                step=step,
            )
        except Exception as e:
            logger.error(f"Suggest response stream failed: {e}", exc_info=True)
            return SuggestResponseIterationResult(
                should_stop=True, is_final=True, reason="error", response_dict=None
            )

        response_dict = _reorder_output_terminal_tools_last(stream_result.response_dict)
        _add_response_to_accumulator(accumulator, response_dict, conversation_id)

        has_tools = _has_tool_calls(response_dict)
        terminal_only = _is_terminal_tool_only(response_dict)
        has_terminal = _has_terminal_tool(response_dict)
        is_final = not has_tools or terminal_only or has_terminal

        terminal_tool_failed = False
        if has_tools:
            terminal_tool_failed = await self.tool_executor.execute_tool_calls(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                fan_page_id=fan_page_id,
                page_scope_user_id=page_scope_user_id,
                run_id=run_id,
                iteration_index=current_iteration,
                response_dict=response_dict,
                accumulator=accumulator,
                num_suggestions=num_suggestions,
                step=step,
            )

        # Override: if a terminal tool failed (e.g. generate_suggestions media validation), allow
        # agent to see the error and retry on the next iteration instead of stopping.
        if terminal_tool_failed and is_final:
            logger.info(
                "generate_suggestions failed validation on iteration %d, "
                "overriding is_final to allow retry",
                current_iteration,
            )
            is_final = False

        # Append to Redis and continue only when not final (next iteration will use updated context)
        if not is_final:
            # Inject iteration warning if approaching limit
            temp_messages = accumulator.to_sorted_messages()
            SuggestResponseIterationWarningInjector.inject_warning(
                temp_messages=temp_messages,
                current_iteration=current_iteration,
                max_iteration=max_iteration,
            )
            entries = _convert_to_openai_entries(temp_messages)
            await self.redis_manager.append_openai_messages_to_temp_context(
                user_id=user_id,
                conversation_id=conversation_id,
                agent_resp_id=agent_response_id,
                new_messages=entries,
            )

        return SuggestResponseIterationResult(
            should_stop=is_final,
            is_final=is_final,
            reason="completed" if is_final else "continue",
            response_dict=response_dict,
        )
