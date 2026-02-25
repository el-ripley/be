"""Select playbooks tool - BaseTool for playbook selection agent."""

import time
import uuid
from typing import Any, Dict

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse


def build_select_playbooks_definition() -> Dict[str, Any]:
    """OpenAI tool definition for select_playbooks."""
    return {
        "type": "function",
        "name": "select_playbooks",
        "description": "Confirm which playbooks to use for this conversation, or none. Ends the playbook selection process. Call this after reviewing search results, or immediately with empty selected_ids if no playbook applies. You must call this exactly once to finish.",
        "parameters": {
            "type": "object",
            "properties": {
                "selected_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of playbook_id UUIDs to use. Empty array if none apply.",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief reason for the selection (use empty string if none).",
                },
            },
            "required": ["selected_ids", "reason"],
            "additionalProperties": False,
        },
        "strict": True,
    }


class SelectPlaybooksTool(BaseTool):
    """Tool to confirm selected playbook IDs and end selection."""

    @property
    def name(self) -> str:
        return "select_playbooks"

    @property
    def definition(self) -> Dict[str, Any]:
        return self._apply_description_override(build_select_playbooks_definition())

    async def execute(
        self,
        conn: Any,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Return selected_ids for the handler."""
        selected_ids = list(arguments.get("selected_ids") or [])
        selected_ids = [str(x) for x in selected_ids if x]
        return {"selected_ids": selected_ids}

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Build function_call_output MessageResponse."""
        current_time = int(time.time() * 1000)
        out_id = str(uuid.uuid4())
        selected_ids = raw_result.get("selected_ids", [])
        function_output = {
            "selected_count": len(selected_ids),
            "selected_ids": selected_ids,
        }
        output_message = MessageResponse(
            id=out_id,
            conversation_id=context.conv_id,
            sequence_number=0,
            type="function_call_output",
            role="tool",
            content=None,
            call_id=context.call_id,
            function_name=self.name,
            function_output=function_output,
            status="completed",
            metadata=None,
            created_at=current_time,
            updated_at=current_time,
        )
        return ToolResult(
            output_message=output_message,
            human_message=None,
            metadata={"selected_ids": selected_ids},
        )
