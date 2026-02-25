"""Complete task tool - terminal tool for suggest_response_agent when no message output is needed.

When triggered by general_agent, the suggest_response agent can call complete_task instead of
generate_suggestions when it has finished processing (e.g., handling an escalation) without
needing to send a message to the customer.
"""

import time
import uuid
from typing import Any, Dict

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse


def build_complete_task_definition() -> Dict[str, Any]:
    """Build OpenAI tool definition for complete_task.

    Returns:
        OpenAI tool definition dict
    """
    return {
        "type": "function",
        "name": "complete_task",
        "description": "Call this when no customer-facing reply is needed for this trigger.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    }


def _create_function_call_output(
    conv_id: str, call_id: str, function_output: Any
) -> MessageResponse:
    """Build function_call_output MessageResponse."""
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


class CompleteTaskTool(BaseTool):
    """Terminal tool for completing work without generating message suggestions."""

    @property
    def name(self) -> str:
        return "complete_task"

    @property
    def definition(self) -> Dict[str, Any]:
        return build_complete_task_definition()

    async def execute(
        self,
        conn: Any,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Return arguments as raw result - no validation needed."""
        return arguments

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Build function_call_output message with task summary."""
        output_message = _create_function_call_output(
            conv_id=context.conv_id,
            call_id=context.call_id,
            function_output=raw_result if isinstance(raw_result, dict) else {},
        )
        return ToolResult(
            output_message=output_message,
            human_message=None,
            metadata=None,
        )
