import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from openai.types.responses import ParsedResponse

from src.agent.common.conversation_settings import get_reasoning_param
from src.agent.common.metadata_types import MessageMetadata
from src.agent.general_agent.utils.temp_message_accumulator import (
    TempMessageAccumulator,
)
from src.api.openai_conversations.schemas import MessageResponse
from src.socket_service import SocketService
from src.utils.logger import get_logger

logger = get_logger()


@dataclass
class StreamResult:
    """Result from LLM stream with status and error details."""

    response_dict: Dict[str, Any]
    accumulator: TempMessageAccumulator
    status: str  # 'completed' | 'failed' (failed includes incomplete/refusal, check error_details.type)
    error_details: Optional[Dict[str, Any]] = None


class LLMStreamHandler:
    """Handle streaming events from the OpenAI Response API and emit socket updates."""

    def __init__(self, socket_service: SocketService) -> None:
        self.socket_service = socket_service

    def _build_tools_with_web_search(
        self, function_tools: List[Dict[str, Any]], settings: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Build tools array combining function tools and web search if enabled."""
        tools = list(function_tools)

        # Add web_search if enabled (default: True)
        if settings.get("web_search_enabled", True):
            tools.append({"type": "web_search"})

        return tools

    async def stream(
        self,
        run_config,
        branch_context,
        temp_context,
        tools,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> StreamResult:
        reasoning = run_config.settings.get("reasoning", "medium")
        verbosity = run_config.settings.get("verbosity", "medium")
        model = run_config.model

        # Get reasoning parameter (None for gpt-5.2 with reasoning=none, otherwise dict)
        reasoning_param = get_reasoning_param(model, reasoning)

        final_response = None
        accumulator = TempMessageAccumulator()
        stream_status = "completed"
        error_details = None
        refusal_text = ""

        start_time_ms = int(time.time() * 1000)

        # Build tools with web search if enabled
        combined_tools = self._build_tools_with_web_search(tools, run_config.settings)

        # Build stream parameters
        stream_params = {
            "model": model,
            "input": temp_context,
            "tools": combined_tools,
            "text": {"verbosity": verbosity},
        }

        # Only add reasoning if it's not None
        if reasoning_param is not None:
            stream_params["reasoning"] = reasoning_param

        try:
            async for event in run_config.llm_call.stream(**stream_params):
                if hasattr(event, "type"):
                    # Read event type directly from attribute (avoid model_dump on every event)
                    event_type = getattr(event, "type", "")

                    if event_type == "response.created":
                        await self.socket_service.emit_agent_event(
                            user_id=run_config.user_id,
                            conv_id=run_config.conversation_id,
                            branch_id=branch_context.current_branch_id,
                            agent_response_id=branch_context.agent_response_id,
                            event_name="response.created",
                            subagent_metadata=subagent_metadata,
                        )

                    elif event_type == "response.output_item.added":
                        # Needs item dict - use model_dump (infrequent event)
                        event_dict = (
                            event.model_dump(mode="json")
                            if hasattr(event, "model_dump")
                            else {}
                        )
                        await self._handle_output_item_added(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            accumulator,
                            subagent_metadata,
                        )

                    elif event_type == "response.reasoning_summary_part.added":
                        # Lightweight: only needs item_id and summary_index
                        event_dict = {
                            "item_id": getattr(event, "item_id", None),
                            "summary_index": getattr(event, "summary_index", 0),
                        }
                        await self._handle_reasoning_summary_part_added(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            accumulator,
                            subagent_metadata,
                        )

                    elif event_type == "response.reasoning_summary_text.delta":
                        # HOT PATH: only needs item_id, delta, summary_index
                        event_dict = {
                            "item_id": getattr(event, "item_id", None),
                            "delta": getattr(event, "delta", ""),
                            "summary_index": getattr(event, "summary_index", 0),
                        }
                        await self._handle_reasoning_summary_text_delta(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            accumulator,
                            subagent_metadata,
                        )

                    elif event_type == "response.reasoning_summary_part.done":
                        # Lightweight: only needs item_id
                        event_dict = {
                            "item_id": getattr(event, "item_id", None),
                        }
                        await self._handle_reasoning_summary_part_done(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            accumulator,
                            subagent_metadata,
                        )

                    elif event_type == "response.content_part.added":
                        # Lightweight: only needs item_id
                        event_dict = {
                            "item_id": getattr(event, "item_id", None),
                        }
                        await self._handle_content_part_added(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            accumulator,
                            subagent_metadata,
                        )

                    elif event_type == "response.output_text.delta":
                        # HOT PATH (most frequent): only needs item_id and delta
                        event_dict = {
                            "item_id": getattr(event, "item_id", None),
                            "delta": getattr(event, "delta", ""),
                        }
                        await self._handle_output_text_delta(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            accumulator,
                            subagent_metadata,
                        )

                    elif event_type == "response.content_part.done":
                        # Lightweight: only needs item_id
                        event_dict = {
                            "item_id": getattr(event, "item_id", None),
                        }
                        await self._handle_content_part_done(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            accumulator,
                            subagent_metadata,
                        )

                    elif event_type == "response.output_item.done":
                        # Needs full item dict - use model_dump (infrequent, once per output item)
                        event_dict = (
                            event.model_dump(mode="json")
                            if hasattr(event, "model_dump")
                            else {}
                        )
                        await self._handle_output_item_done(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            accumulator,
                            subagent_metadata,
                        )

                    elif event_type == "response.web_search_call.in_progress":
                        # Lightweight: needs item_id and action
                        action = getattr(event, "action", None)
                        event_dict = {
                            "item_id": getattr(event, "item_id", None),
                            "action": action.model_dump(mode="json")
                            if hasattr(action, "model_dump")
                            else (action or {}),
                        }
                        await self._handle_web_search_in_progress(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            accumulator,
                            subagent_metadata,
                        )

                    elif event_type == "response.web_search_call.searching":
                        # Lightweight: needs action with query
                        action = getattr(event, "action", None)
                        event_dict = {
                            "action": action.model_dump(mode="json")
                            if hasattr(action, "model_dump")
                            else (action or {}),
                        }
                        await self._handle_web_search_searching(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            subagent_metadata,
                        )

                    elif event_type == "response.web_search_call.completed":
                        await self._handle_web_search_completed(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            {},
                            accumulator,
                            subagent_metadata,
                        )

                    elif event_type == "response.failed":
                        # Needs full response for error details - use model_dump (rare event)
                        event_dict = (
                            event.model_dump(mode="json")
                            if hasattr(event, "model_dump")
                            else {}
                        )
                        (
                            stream_status,
                            error_details,
                        ) = await self._handle_response_failed(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                        )

                    elif event_type == "response.incomplete":
                        # Needs full response for details - use model_dump (rare event)
                        event_dict = (
                            event.model_dump(mode="json")
                            if hasattr(event, "model_dump")
                            else {}
                        )
                        (
                            stream_status,
                            error_details,
                        ) = await self._handle_response_incomplete(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                        )

                    elif event_type == "response.refusal.delta":
                        # Lightweight: only needs delta
                        event_dict = {
                            "delta": getattr(event, "delta", ""),
                        }
                        refusal_text = await self._handle_refusal_delta(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            refusal_text,
                        )

                    elif event_type == "response.refusal.done":
                        # Lightweight: only needs refusal text and item_id
                        event_dict = {
                            "refusal": getattr(event, "refusal", ""),
                            "item_id": getattr(event, "item_id", None),
                        }
                        stream_status, error_details = await self._handle_refusal_done(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                            refusal_text,
                        )

                    elif event_type == "error":
                        # Needs full error info - use model_dump (rare event)
                        event_dict = (
                            event.model_dump(mode="json")
                            if hasattr(event, "model_dump")
                            else {}
                        )
                        stream_status, error_details = await self._handle_stream_error(
                            run_config.user_id,
                            run_config.conversation_id,
                            branch_context.current_branch_id,
                            branch_context.agent_response_id,
                            event_dict,
                        )

                if isinstance(event, ParsedResponse):
                    final_response = event

        except Exception as e:
            # Catch LLM API errors (e.g. 400 from stale image URLs, network errors)
            # so they don't crash the entire agent run. Instead, return a failed
            # StreamResult that the iteration runner can handle gracefully.
            logger.error(f"LLM stream exception: {str(e)}")
            stream_status = "failed"

            error_code = "llm_stream_exception"
            error_message = str(e)
            if hasattr(e, "status_code"):
                error_code = f"http_{e.status_code}"

            error_details = {
                "code": error_code,
                "message": error_message,
            }

            # Emit error to frontend so user gets informed
            try:
                await self.socket_service.emit_agent_error(
                    user_id=run_config.user_id,
                    conv_id=run_config.conversation_id,
                    error_type="llm_error",
                    code=error_code,
                    message=error_message,
                    branch_id=branch_context.current_branch_id,
                    agent_response_id=branch_context.agent_response_id,
                )
            except Exception as emit_err:
                logger.error(f"Failed to emit LLM error to frontend: {emit_err}")

        # If we got a failed/incomplete/refusal event, we may not have a final_response
        # Create a response_dict for DB storage
        if final_response is None:
            if stream_status == "failed":
                # Try to use response from error_details (contains usage for billing)
                if error_details and error_details.get("response"):
                    response_dict = error_details.pop("response")
                else:
                    # Fallback minimal response dict
                    # Use a unique ID to avoid duplicate key constraint violations
                    # when multiple failed attempts save to openai_response table
                    response_dict = {
                        "id": f"failed_{uuid.uuid4().hex[:16]}",
                        "created": int(time.time() * 1000),
                        "status": "failed",
                        "output": [],
                        "usage": {},
                    }
            else:
                raise RuntimeError("LLM stream completed without a final response")
        else:
            response_dict = final_response.model_dump(mode="json")

        end_time_ms = int(time.time() * 1000)
        latency_ms = end_time_ms - start_time_ms
        response_dict["latency_ms"] = latency_ms

        return StreamResult(
            response_dict=response_dict,
            accumulator=accumulator,
            status=stream_status,
            error_details=error_details,
        )

    async def _handle_output_item_added(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        accumulator: TempMessageAccumulator,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle output_item.added - emit event with type."""
        item = event_dict.get("item", {})
        item_type = item.get("type")
        item_id = item.get("id")

        if not item_id or not item_type:
            return

        msg_type = None
        if item_type == "message":
            msg_type = "message"
        elif item_type == "reasoning":
            msg_type = "reasoning"
        elif item_type == "function_call":
            msg_type = "function_call"

        if msg_type:
            accumulator.set_message_type(item_id, msg_type)

        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conv_id,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
            msg_type=msg_type,
            event_name="output_item.added",
            subagent_metadata=subagent_metadata,
        )

    async def _handle_reasoning_summary_part_added(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        accumulator: TempMessageAccumulator,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Create temp message for reasoning and emit part.added event."""
        item_id = event_dict.get("item_id")
        summary_index = event_dict.get("summary_index", 0)

        if not item_id:
            return

        msg_uuid = accumulator.get_message_uuid(item_id)
        if not msg_uuid:
            msg_uuid = str(uuid.uuid4())
            accumulator.set_message_uuid(item_id, msg_uuid)

        if msg_uuid not in accumulator.temp_messages_map:
            current_time = int(time.time() * 1000)
            message = MessageResponse(
                id=msg_uuid,
                conversation_id=conv_id,
                sequence_number=0,
                type="reasoning",
                role="assistant",
                content=None,
                reasoning_summary=[],
                status="in_progress",
                created_at=current_time,
                updated_at=current_time,
            )
            accumulator.store_message(message)

            client_msg = message.model_dump(mode="json")
            await self.socket_service.emit_agent_event(
                user_id=user_id,
                conv_id=conv_id,
                branch_id=branch_id,
                agent_response_id=agent_resp_id,
                msg_type="reasoning",
                event_name="part.added",
                msg_item=client_msg,
                subagent_metadata=subagent_metadata,
            )

        message = accumulator.temp_messages_map[msg_uuid]
        if message.reasoning_summary is None:
            message.reasoning_summary = []

        while len(message.reasoning_summary) <= summary_index:
            message.reasoning_summary.append({"text": "", "type": "summary_text"})

    async def _handle_reasoning_summary_text_delta(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        accumulator: TempMessageAccumulator,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle streaming reasoning text delta."""
        item_id = event_dict.get("item_id")
        delta = event_dict.get("delta", "")
        summary_index = event_dict.get("summary_index", 0)

        if not item_id:
            return

        msg_uuid = accumulator.get_message_uuid(item_id)
        if not msg_uuid or msg_uuid not in accumulator.temp_messages_map:
            return

        message = accumulator.temp_messages_map[msg_uuid]
        if message.reasoning_summary is None:
            message.reasoning_summary = []

        while len(message.reasoning_summary) <= summary_index:
            message.reasoning_summary.append({"text": "", "type": "summary_text"})

        if isinstance(message.reasoning_summary[summary_index].get("text"), str):
            message.reasoning_summary[summary_index]["text"] += delta
        else:
            message.reasoning_summary[summary_index]["text"] = delta

        message.updated_at = int(time.time() * 1000)

        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conv_id,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
            msg_type="reasoning",
            event_name="delta",
            msg_id=msg_uuid,
            delta=delta,
            subagent_metadata=subagent_metadata,
        )

    async def _handle_reasoning_summary_part_done(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        accumulator: TempMessageAccumulator,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle when reasoning summary part is done."""
        item_id = event_dict.get("item_id")

        if not item_id:
            return

        msg_uuid = accumulator.get_message_uuid(item_id)
        if not msg_uuid:
            return

        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conv_id,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
            msg_type="reasoning",
            event_name="part.done",
            msg_id=msg_uuid,
            subagent_metadata=subagent_metadata,
        )

    async def _handle_content_part_added(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        accumulator: TempMessageAccumulator,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Create temp message for content and emit part.added event."""
        item_id = event_dict.get("item_id")

        if not item_id:
            return

        msg_uuid = accumulator.get_message_uuid(item_id)
        if not msg_uuid:
            msg_uuid = str(uuid.uuid4())
            accumulator.set_message_uuid(item_id, msg_uuid)

        if msg_uuid not in accumulator.temp_messages_map:
            current_time = int(time.time() * 1000)
            message = MessageResponse(
                id=msg_uuid,
                conversation_id=conv_id,
                sequence_number=0,
                type="message",
                role="assistant",
                content="",
                status="in_progress",
                created_at=current_time,
                updated_at=current_time,
            )
            accumulator.store_message(message)

            client_msg = message.model_dump(mode="json")
            await self.socket_service.emit_agent_event(
                user_id=user_id,
                conv_id=conv_id,
                branch_id=branch_id,
                agent_response_id=agent_resp_id,
                msg_type="message",
                event_name="part.added",
                msg_item=client_msg,
                subagent_metadata=subagent_metadata,
            )

    async def _handle_output_text_delta(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        accumulator: TempMessageAccumulator,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle streaming text delta for message content."""
        item_id = event_dict.get("item_id")
        delta = event_dict.get("delta", "")

        if not item_id:
            return

        msg_uuid = accumulator.get_message_uuid(item_id)
        if not msg_uuid or msg_uuid not in accumulator.temp_messages_map:
            return

        message = accumulator.temp_messages_map[msg_uuid]
        if isinstance(message.content, str):
            message.content += delta
        else:
            message.content = delta
        message.updated_at = int(time.time() * 1000)

        msg_type = accumulator.get_message_type(item_id, "message")

        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conv_id,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
            msg_type=msg_type,
            event_name="delta",
            msg_id=msg_uuid,
            delta=delta,
            subagent_metadata=subagent_metadata,
        )

    async def _handle_content_part_done(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        accumulator: TempMessageAccumulator,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle when content part is done - reconstruct content with annotations."""
        item_id = event_dict.get("item_id")

        if not item_id:
            return

        msg_uuid = accumulator.get_message_uuid(item_id)
        if not msg_uuid:
            return

        msg_type = accumulator.get_message_type(item_id, "message")

        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conv_id,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
            msg_type=msg_type,
            event_name="part.done",
            msg_id=msg_uuid,
            subagent_metadata=subagent_metadata,
        )

    async def _handle_output_item_done(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        accumulator: TempMessageAccumulator,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle when output item is done."""
        item = event_dict.get("item", {})
        item_id = item.get("id")
        item_type = item.get("type")

        if not item_id:
            return

        msg_type = accumulator.get_message_type(item_id)
        current_time = int(time.time() * 1000)

        if item_type == "function_call":
            call_id = item.get("call_id")
            name = item.get("name")
            arguments_str = item.get("arguments", "{}")

            try:
                args_dict = json.loads(arguments_str)
            except json.JSONDecodeError:
                args_dict = {}

            meta: MessageMetadata = {}
            item_id_val = args_dict.get("item_id")
            item_type_val = args_dict.get("item_type")
            if item_id_val and item_type_val:
                meta = {
                    "source": "fb_context_fetch",
                    "item_id": item_id_val,
                    "item_type": item_type_val,
                    "cursor": args_dict.get("cursor"),
                    "limit": args_dict.get("limit"),
                }

            msg_uuid = str(uuid.uuid4())
            accumulator.set_message_uuid(item_id, msg_uuid)
            current_time = int(time.time() * 1000)

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
                metadata=meta or None,
                created_at=current_time,
                updated_at=current_time,
            )
            accumulator.store_message(message)

            client_msg = message.model_dump(mode="json")
            await self.socket_service.emit_agent_event(
                user_id=user_id,
                conv_id=conv_id,
                branch_id=branch_id,
                agent_response_id=agent_resp_id,
                msg_type="function_call",
                event_name="output_item.done",
                msg_item=client_msg,
                subagent_metadata=subagent_metadata,
            )

        elif item_type == "web_search_call":
            # Finalize web_search_call message with action from item
            action = item.get("action", {})
            status = item.get("status", "completed")

            msg_uuid = accumulator.get_message_uuid(item_id)
            if not msg_uuid:
                msg_uuid = str(uuid.uuid4())
                accumulator.set_message_uuid(item_id, msg_uuid)

            # Reuse temp message created at `web_search_call.in_progress` if available
            if msg_uuid in accumulator.temp_messages_map:
                message = accumulator.temp_messages_map[msg_uuid]
                message.type = "web_search_call"
                message.role = "assistant"
                message.web_search_action = action
                message.status = status
                message.updated_at = current_time
            else:
                message = MessageResponse(
                    id=msg_uuid,
                    conversation_id=conv_id,
                    sequence_number=0,
                    type="web_search_call",
                    role="assistant",
                    content=None,
                    web_search_action=action,
                    status=status,
                    created_at=current_time,
                    updated_at=current_time,
                )
                accumulator.store_message(message)

            client_msg = message.model_dump(mode="json")
            await self.socket_service.emit_agent_event(
                user_id=user_id,
                conv_id=conv_id,
                branch_id=branch_id,
                agent_response_id=agent_resp_id,
                msg_type="web_search_call",
                event_name="output_item.done",
                msg_item=client_msg,
                subagent_metadata=subagent_metadata,
            )

        elif item_type == "message":
            # Finalize normal assistant message with full content (including annotations)
            msg_uuid = accumulator.get_message_uuid(item_id)
            if not msg_uuid:
                msg_uuid = str(uuid.uuid4())
                accumulator.set_message_uuid(item_id, msg_uuid)

            content = item.get("content", [])
            status = item.get("status", "completed")

            if msg_uuid in accumulator.temp_messages_map:
                message = accumulator.temp_messages_map[msg_uuid]
                message.type = "message"
                message.role = "assistant"
                message.content = content
                message.status = status
                message.updated_at = current_time
                # Ensure metadata.source is set for AI messages
                if not message.metadata:
                    message.metadata = {}
                if "source" not in message.metadata:
                    message.metadata["source"] = "assistant"
            else:
                ai_metadata: MessageMetadata = {"source": "assistant"}
                message = MessageResponse(
                    id=msg_uuid,
                    conversation_id=conv_id,
                    sequence_number=0,
                    type="message",
                    role="assistant",
                    content=content,
                    status=status,
                    metadata=ai_metadata,
                    created_at=current_time,
                    updated_at=current_time,
                )
                accumulator.store_message(message)

            client_msg = message.model_dump(mode="json")
            await self.socket_service.emit_agent_event(
                user_id=user_id,
                conv_id=conv_id,
                branch_id=branch_id,
                agent_response_id=agent_resp_id,
                msg_type=msg_type or "message",
                event_name="output_item.done",
                msg_item=client_msg,
                subagent_metadata=subagent_metadata,
            )

        else:
            if msg_type:
                await self.socket_service.emit_agent_event(
                    user_id=user_id,
                    conv_id=conv_id,
                    branch_id=branch_id,
                    agent_response_id=agent_resp_id,
                    msg_type=msg_type,
                    event_name="output_item.done",
                    subagent_metadata=subagent_metadata,
                )

    async def _handle_response_failed(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """Handle response.failed event - fatal error."""
        response = event_dict.get("response", {})
        error = response.get("error", {})

        error_code = error.get("code", "unknown_error")
        error_message = error.get("message", "The model failed to generate a response.")

        # Store full response for DB (may contain usage data for billing)
        error_details = {
            "code": error_code,
            "message": error_message,
            "response_id": response.get("id"),
            "response": response,  # Full response for DB storage
        }

        await self.socket_service.emit_agent_error(
            user_id=user_id,
            conv_id=conv_id,
            error_type="failed",
            code=error_code,
            message=error_message,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
        )

        return "failed", error_details

    async def _handle_response_incomplete(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """Handle response.incomplete event - max_tokens reached."""
        response = event_dict.get("response", {})
        incomplete_details = response.get("incomplete_details", {})
        reason = incomplete_details.get("reason", "max_tokens")

        # Store full response for DB (contains usage data for billing)
        error_details = {
            "type": "incomplete",
            "reason": reason,
            "response_id": response.get("id"),
            "response": response,  # Full response for DB storage
        }

        await self.socket_service.emit_agent_warning(
            user_id=user_id,
            conv_id=conv_id,
            warning_type="incomplete",
            reason=reason,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
            has_partial_content=True,
        )

        # Use 'failed' status for DB compatibility, but store type in error_details
        return "failed", error_details

    async def _handle_refusal_delta(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        refusal_text: str,
    ) -> str:
        """Handle response.refusal.delta - accumulate refusal text."""
        delta = event_dict.get("delta", "")
        return refusal_text + delta

    async def _handle_refusal_done(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        refusal_text: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """Handle response.refusal.done - model refused to respond."""
        final_refusal = event_dict.get("refusal", refusal_text)

        error_details = {
            "type": "refusal",
            "refusal_text": final_refusal,
            "item_id": event_dict.get("item_id"),
        }

        await self.socket_service.emit_agent_warning(
            user_id=user_id,
            conv_id=conv_id,
            warning_type="refusal",
            reason=final_refusal,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
            has_partial_content=False,
        )

        # Use 'failed' status for DB compatibility, but store type in error_details
        return "failed", error_details

    async def _handle_stream_error(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """Handle error event - stream error."""
        error_code = event_dict.get("code", "stream_error")
        error_message = event_dict.get("message", "An error occurred during streaming.")

        error_details = {
            "code": error_code,
            "message": error_message,
            "param": event_dict.get("param"),
        }

        await self.socket_service.emit_agent_error(
            user_id=user_id,
            conv_id=conv_id,
            error_type="stream_error",
            code=error_code,
            message=error_message,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
        )

        return "failed", error_details

    async def _handle_web_search_in_progress(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        accumulator: TempMessageAccumulator,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle web_search_call.in_progress.

        - Set type in accumulator
        - Create a temporary web_search_call message with stable uuid
        - Emit it so FE can render a loading/searching card early.
        """
        item_id = event_dict.get("item_id")
        action = event_dict.get("action", {})

        if not item_id:
            return

        accumulator.set_message_type(item_id, "web_search_call")

        msg_uuid = accumulator.get_message_uuid(item_id)
        if not msg_uuid:
            msg_uuid = str(uuid.uuid4())
            accumulator.set_message_uuid(item_id, msg_uuid)

        current_time = int(time.time() * 1000)

        # Create or update temp message for web_search_call
        if msg_uuid in accumulator.temp_messages_map:
            message = accumulator.temp_messages_map[msg_uuid]
            message.type = "web_search_call"
            message.role = "assistant"
            message.web_search_action = action or getattr(
                message, "web_search_action", None
            )
            message.status = "in_progress"
            message.updated_at = current_time
        else:
            message = MessageResponse(
                id=msg_uuid,
                conversation_id=conv_id,
                sequence_number=0,
                type="web_search_call",
                role="assistant",
                content=None,
                web_search_action=action,
                status="in_progress",
                created_at=current_time,
                updated_at=current_time,
            )
            accumulator.store_message(message)

        client_msg = message.model_dump(mode="json")

        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conv_id,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
            msg_type="web_search_call",
            event_name="web_search.in_progress",
            msg_item=client_msg,
            subagent_metadata=subagent_metadata,
        )

    async def _handle_web_search_searching(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle web_search_call.searching - emit search query."""
        action = event_dict.get("action", {})
        query = action.get("query", "")

        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conv_id,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
            msg_type="web_search_call",
            event_name="web_search.searching",
            msg_item={"query": query},
            subagent_metadata=subagent_metadata,
        )

    async def _handle_web_search_completed(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_resp_id: str,
        event_dict: Dict[str, Any],
        accumulator: TempMessageAccumulator,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle web_search_call.completed - emit completion event."""
        # Web search call message will be created in _handle_output_item_done
        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conv_id,
            branch_id=branch_id,
            agent_response_id=agent_resp_id,
            msg_type="web_search_call",
            event_name="web_search.completed",
            subagent_metadata=subagent_metadata,
        )
