"""Stream handler for Suggest Response LLM calls."""

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openai.types.responses import ParsedResponse

from src.agent.core.llm_call import LLM_call
from src.agent.suggest_response.socket.emitter import SuggestResponseSocketEmitter
from src.utils.logger import get_logger

logger = get_logger()


@dataclass
class SuggestResponseStreamResult:
    """Result from LLM stream with status and error details."""

    response_dict: Dict[str, Any]
    latency_ms: int
    status: str  # 'completed' | 'failed'
    error_details: Optional[Dict[str, Any]] = None


class SuggestResponseStreamHandler:
    """Handle streaming events from OpenAI Response API for suggest response."""

    def __init__(self, socket_emitter: SuggestResponseSocketEmitter):
        self.socket_emitter = socket_emitter

    async def stream(
        self,
        llm_call: LLM_call,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        iteration_index: int,
        input_messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        reasoning_param: Optional[Dict[str, Any]],
        verbosity: str = "low",
        step: Optional[str] = None,
    ) -> SuggestResponseStreamResult:
        """
        Stream LLM call and handle events.

        Emits: reasoning.started, reasoning.delta, reasoning.done, tool_call.started.
        Errors are raised; caller (runner) should emit run.error with run_id.
        """
        start_time_ms = int(time.time() * 1000)
        final_response = None
        stream_error = None

        reasoning_msg_id: Optional[str] = None
        reasoning_content: List[str] = []

        stream_params = {
            "model": model,
            "input": input_messages,
            "tools": tools,
            "tool_choice": "required",
            "text": {"verbosity": verbosity},
        }
        if reasoning_param is not None:
            stream_params["reasoning"] = reasoning_param

        async for event in llm_call.stream(**stream_params):
            if hasattr(event, "type"):
                event_dict = (
                    event.model_dump(mode="json")
                    if hasattr(event, "model_dump")
                    else {}
                )
                event_type = event_dict.get("type", "")

                if event_type == "response.reasoning_summary_part.added":
                    reasoning_msg_id = str(uuid.uuid4())
                    reasoning_content = []
                    await self.socket_emitter.emit_reasoning_started(
                        user_id=user_id,
                        conversation_type=conversation_type,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        iteration_index=iteration_index,
                        msg_id=reasoning_msg_id,
                        step=step,
                    )

                elif event_type == "response.reasoning_summary_text.delta":
                    delta = event_dict.get("delta", "")
                    reasoning_content.append(delta)
                    if reasoning_msg_id:
                        await self.socket_emitter.emit_reasoning_delta(
                            user_id=user_id,
                            conversation_type=conversation_type,
                            conversation_id=conversation_id,
                            run_id=run_id,
                            msg_id=reasoning_msg_id,
                            delta=delta,
                            step=step,
                        )

                elif event_type == "response.reasoning_summary_part.done":
                    content = "".join(reasoning_content) if reasoning_content else ""
                    if reasoning_msg_id:
                        await self.socket_emitter.emit_reasoning_done(
                            user_id=user_id,
                            conversation_type=conversation_type,
                            conversation_id=conversation_id,
                            run_id=run_id,
                            msg_id=reasoning_msg_id,
                            content=content,
                            step=step,
                        )
                    reasoning_msg_id = None
                    reasoning_content = []

                elif event_type == "response.output_item.done":
                    item = event_dict.get("item", {})
                    if item.get("type") == "function_call":
                        call_id = item.get("call_id")
                        name = item.get("name")
                        arguments_str = item.get("arguments", "{}")
                        try:
                            arguments = json.loads(arguments_str)
                        except json.JSONDecodeError:
                            arguments = {}
                        msg_uuid = str(uuid.uuid4())
                        current_time = int(time.time() * 1000)
                        msg_item = {
                            "id": msg_uuid,
                            "conversation_id": conversation_id,
                            "sequence_number": 0,
                            "type": "function_call",
                            "role": "assistant",
                            "content": None,
                            "call_id": call_id,
                            "function_name": name,
                            "function_arguments": arguments,
                            "status": "completed",
                            "metadata": None,
                            "created_at": current_time,
                            "updated_at": current_time,
                        }
                        await self.socket_emitter.emit_tool_call_started(
                            user_id=user_id,
                            conversation_type=conversation_type,
                            conversation_id=conversation_id,
                            run_id=run_id,
                            iteration_index=iteration_index,
                            msg_item=msg_item,
                            step=step,
                        )

                elif event_type == "response.failed":
                    response = event_dict.get("response", {})
                    error = response.get("error", {})
                    stream_error = {
                        "type": "failed",
                        "code": error.get("code", "unknown_error"),
                        "message": error.get(
                            "message", "The model failed to generate a response."
                        ),
                    }
                    raise RuntimeError(stream_error.get("message", "Unknown error"))

                elif event_type == "response.incomplete":
                    response = event_dict.get("response", {})
                    incomplete_details = response.get("incomplete_details", {})
                    reason = incomplete_details.get("reason", "max_tokens")
                    stream_error = {"type": "incomplete", "reason": reason}
                    raise RuntimeError(f"Response incomplete: {reason}")

                elif event_type == "error":
                    error_code = event_dict.get("code", "stream_error")
                    error_message = event_dict.get(
                        "message", "An error occurred during streaming."
                    )
                    stream_error = {
                        "type": "stream_error",
                        "code": error_code,
                        "message": error_message,
                    }
                    raise RuntimeError(error_message)

            if isinstance(event, ParsedResponse):
                final_response = event

        if final_response is None:
            raise RuntimeError("LLM stream completed without a final response")

        response_dict = final_response.model_dump(mode="json")
        end_time_ms = int(time.time() * 1000)
        latency_ms = end_time_ms - start_time_ms

        return SuggestResponseStreamResult(
            response_dict=response_dict,
            latency_ms=latency_ms,
            status="completed",
            error_details=None,
        )
