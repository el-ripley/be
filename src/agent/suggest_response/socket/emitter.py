"""Socket emitter for Suggest Response events - centralized socket emissions.

Unified event hierarchy:
  run.started
    step.started  { step }
      iteration.started  { step, iteration_index }
        reasoning.started / .delta / .done  { step, ... }
        tool_call.started  { step, iteration_index, msg_item }
        tool_result        { step, iteration_index, msg_item }
      iteration.done  { step, iteration_index, has_more }
    step.completed  { step, result? }
  run.completed | run.error
"""

from typing import Any, Dict, List, Literal, Optional

from src.socket_service import SocketService

StepName = Literal["playbook_retrieval", "response_generation"]


class SuggestResponseSocketEmitter:
    """Centralized socket emitter for suggest response events."""

    def __init__(self, socket_service: SocketService):
        self.socket_service = socket_service

    # ── run-level ──────────────────────────────────────────────

    async def emit_run_started(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
    ) -> None:
        """Emit run.started event at the beginning of agent run."""
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="run.started",
            data={"run_id": run_id},
        )

    async def emit_run_completed(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        history_id: str,
        suggestions: List[Dict[str, Any]],
    ) -> None:
        """Emit run.completed when agent finishes successfully."""
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="run.completed",
            data={
                "run_id": run_id,
                "history_id": history_id,
                "suggestions": suggestions,
                "suggestion_count": len(suggestions),
            },
        )

    async def emit_run_error(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        error: str,
        code: Optional[str] = None,
    ) -> None:
        """Emit run.error when an error occurs."""
        data: Dict[str, Any] = {"run_id": run_id, "error": error}
        if code is not None:
            data["code"] = code
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="run.error",
            data=data,
        )

    # ── step-level ─────────────────────────────────────────────

    async def emit_step_started(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        step: StepName,
    ) -> None:
        """Emit step.started when a run step begins (playbook_retrieval or response_generation)."""
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="step.started",
            data={"run_id": run_id, "step": step},
        )

    async def emit_step_completed(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        step: StepName,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit step.completed when a run step finishes.

        Args:
            result: Optional step-specific result data (e.g. selected_ids for playbook step).
        """
        data: Dict[str, Any] = {"run_id": run_id, "step": step}
        if result is not None:
            data["result"] = result
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="step.completed",
            data=data,
        )

    # ── iteration-level ────────────────────────────────────────

    async def emit_iteration_started(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        iteration_index: int,
        step: Optional[StepName] = None,
    ) -> None:
        """Emit iteration.started event at the beginning of each iteration."""
        data: Dict[str, Any] = {
            "run_id": run_id,
            "iteration_index": iteration_index,
        }
        if step is not None:
            data["step"] = step
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="iteration.started",
            data=data,
        )

    async def emit_iteration_done(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        iteration_index: int,
        has_more: bool,
        step: Optional[StepName] = None,
    ) -> None:
        """Emit iteration.done at the end of each iteration."""
        data: Dict[str, Any] = {
            "run_id": run_id,
            "iteration_index": iteration_index,
            "has_more": has_more,
        }
        if step is not None:
            data["step"] = step
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="iteration.done",
            data=data,
        )

    # ── reasoning events ───────────────────────────────────────

    async def emit_reasoning_started(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        iteration_index: int,
        msg_id: str,
        step: Optional[StepName] = None,
    ) -> None:
        """Emit reasoning.started when LLM begins reasoning."""
        data: Dict[str, Any] = {
            "run_id": run_id,
            "iteration_index": iteration_index,
            "msg_id": msg_id,
        }
        if step is not None:
            data["step"] = step
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="reasoning.started",
            data=data,
        )

    async def emit_reasoning_delta(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        msg_id: str,
        delta: str,
        step: Optional[StepName] = None,
    ) -> None:
        """Emit reasoning.delta for streaming reasoning text."""
        data: Dict[str, Any] = {
            "run_id": run_id,
            "msg_id": msg_id,
            "delta": delta,
        }
        if step is not None:
            data["step"] = step
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="reasoning.delta",
            data=data,
        )

    async def emit_reasoning_done(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        msg_id: str,
        content: str,
        step: Optional[StepName] = None,
    ) -> None:
        """Emit reasoning.done when reasoning is complete."""
        data: Dict[str, Any] = {
            "run_id": run_id,
            "msg_id": msg_id,
            "content": content,
        }
        if step is not None:
            data["step"] = step
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="reasoning.done",
            data=data,
        )

    # ── tool events ────────────────────────────────────────────

    async def emit_tool_call_started(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        iteration_index: int,
        msg_item: Dict[str, Any],
        step: Optional[StepName] = None,
    ) -> None:
        """Emit tool_call.started when LLM outputs function_call."""
        data: Dict[str, Any] = {
            "run_id": run_id,
            "iteration_index": iteration_index,
            "msg_item": msg_item,
        }
        if step is not None:
            data["step"] = step
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="tool_call.started",
            data=data,
        )

    async def emit_tool_result(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        run_id: str,
        iteration_index: int,
        msg_item: Dict[str, Any],
        step: Optional[StepName] = None,
    ) -> None:
        """Emit tool_result after tool execution completes (full msg_item)."""
        data: Dict[str, Any] = {
            "run_id": run_id,
            "iteration_index": iteration_index,
            "msg_item": msg_item,
        }
        if step is not None:
            data["step"] = step
        await self.socket_service.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name="tool_result",
            data=data,
        )
