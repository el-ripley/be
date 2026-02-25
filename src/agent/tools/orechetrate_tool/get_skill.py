"""Get skill tool - load detailed skill documentation for complex operations.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):
   {
     "success": true,
     "skill_name": str,
     "content": str,  # Full markdown content of the skill
     "message": str | None  # Optional message
   }
"""

import uuid
import time
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.agent.general_agent.context.skills import load_skill, load_triggers, list_available_skills
from src.api.openai_conversations.schemas import MessageResponse
from src.utils.logger import get_logger

logger = get_logger()


_TRIGGERS_CONTENT = load_triggers() or ""

TOOL_DESCRIPTION = f"""Load skill documentation for specialized tasks.

Skills provide specialized workflows and domain knowledge. When a skill is relevant, invoke this tool IMMEDIATELY as your first action — NEVER just announce a skill without loading it first.

**TRIGGER CONDITIONS:**

{_TRIGGERS_CONTENT}

**HOW TO USE:**
1. Recognize trigger condition → call this tool immediately
2. Read skill documentation in result
3. Follow the workflow in the skill
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


class GetSkillTool(BaseTool):
    """Tool to load detailed skill documentation."""

    @property
    def name(self) -> str:
        return "get_skill"

    @property
    def definition(self) -> Dict[str, Any]:
        available_skills = list_available_skills()
        return {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "enum": available_skills,
                        "description": "Skill to load: " + ", ".join(available_skills),
                    },
                },
                "required": ["skill_name"],
                "additionalProperties": False,
            },
            "strict": True,
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - load skill documentation."""
        skill_name = arguments.get("skill_name")

        if not skill_name:
            return {
                "success": False,
                "error": "skill_name is required",
            }

        try:
            skill_content = load_skill(skill_name)

            if skill_content is None:
                available = list_available_skills()
                return {
                    "success": False,
                    "error": f"Skill '{skill_name}' not found. Available skills: {', '.join(available)}",
                }

            return {
                "success": True,
                "skill_name": skill_name,
                "content": skill_content,
                "message": f"Loaded skill documentation: {skill_name}",
            }

        except Exception as e:
            logger.error(f"Error loading skill {skill_name}: {str(e)}")
            return {
                "success": False,
                "error": f"Internal error loading skill: {str(e)}",
            }

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
