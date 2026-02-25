"""Task tool - spawn Explore subagent for complex information gathering tasks."""

import uuid
import time
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.agent.general_agent.subagent.subagent_runner import (
    SubAgentRunner,
    SubAgentContext,
    SubAgentResult,
)
from src.api.openai_conversations.schemas import MessageResponse


TASK_TOOL_DESCRIPTION = """Spawn a specialized Explore subagent to gather information from Facebook data and databases.

**CRITICAL**: Use this tool for ANY data exploration task. Direct sql_query is acceptable when: (1) only a few simple queries are needed, (2) you suspect Explore's report may be inaccurate and need to verify, or (3) sensitive write operations that require reading current state first.

**Available subagent types:**
- `Explore`: Read-only information gathering specialist
  - Has access to database queries (sql_query)
  - Can explore conversations, posts, comments, customer data
  - Read-only: queries existing synced data, does NOT sync new data

**When to use this tool:**
- ANY exploration or analysis task (conversations, posts, comments, engagement)
- Questions about "how", "which", "what" related to page data
- Gathering statistics or summaries
- Understanding patterns across multiple items
- ANY task that might require 2+ sql_query calls

**Communication Protocol - IMPORTANT:**
When sending prompts to the Explore agent, be EXPLICIT about what you want:

1. **Specify the exploration goal clearly**
2. **List exactly what information you need** (use numbered list)
3. **Request specific output format** so the report is actionable

**Example prompt structure:**
```
Please explore [target] and provide a comprehensive report. Include:

1. **[Category 1]**: [What specific information]
2. **[Category 2]**: [What specific information]
3. **[Category 3]**: [What specific information]
...

Please format the report with:
- Markdown tables for listing items
- Clear sections matching the numbered list above
- Summary at the end with key insights
```

**Real example:**
```
Please explore conversations with customers who mentioned "refund" in the last 7 days. Include:

1. **Conversation List**: Table with customer_name, last_message_time, message_count
2. **Message Samples**: Show 2-3 relevant messages from each conversation
3. **Sentiment Overview**: General sentiment of these conversations
4. **Common Issues**: What specific issues are customers facing

Format as markdown with tables where appropriate.
```

The Explore agent will:
1. Receive your detailed prompt
2. Use sql_query to gather information from existing data
3. Return a well-formatted report matching your requested structure

**Resume feature:** Continue previous exploration using the subagent_id with `resume` parameter."""


class TaskTool(BaseTool):
    """
    Tool to spawn subagent for complex tasks.

    The subagent runs with streaming - user sees real-time progress.
    Results are displayed under the task tool_call in the UI.
    """

    def __init__(self, subagent_runner: SubAgentRunner):
        self.subagent_runner = subagent_runner

    @property
    def name(self) -> str:
        return "task"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": "task",
            "description": TASK_TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Short description (3-5 words) shown in UI",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Detailed task/instructions for the subagent",
                    },
                    "subagent_type": {
                        "type": "string",
                        "enum": ["Explore"],
                        "description": "The type of specialized agent to use for this task.",
                    },
                    "model": {
                        "type": "string",
                        "enum": ["gpt-5-nano", "gpt-5-mini", "gpt-5.2"],
                        "description": "Model to use (default: inherit from parent)",
                    },
                    "resume": {
                        "type": "string",
                        "description": "Subagent conversation ID to resume from previous execution",
                    },
                    "max_turns": {
                        "type": "integer",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 60,
                        "description": "Maximum iterations (default: 20)",
                    },
                },
                "required": ["description", "prompt", "subagent_type"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> SubAgentResult:
        """
        Execute task tool - spawn and run subagent.

        This is a BLOCKING call - main agent waits for subagent to complete.
        However, subagent streams events to FE so user sees real-time progress.
        """
        # Build subagent context
        subagent_ctx = SubAgentContext(
            user_id=context.user_id,
            parent_conversation_id=context.conv_id,
            parent_agent_response_id=context.agent_response_id,
            parent_branch_id=context.branch_id,
            task_call_id=context.call_id,  # Link with this tool_call
            model=arguments.get("model") or "gpt-5-mini",
            max_turns=arguments.get("max_turns", 20),
            subagent_type=arguments.get("subagent_type", "Explore"),
        )

        # Run subagent (blocks until complete, but streams to FE)
        result = await self.subagent_runner.run(
            ctx=subagent_ctx,
            prompt=arguments["prompt"],
            resume_conversation_id=arguments.get("resume"),
        )

        return result

    def process_result(
        self,
        context: ToolCallContext,
        raw_result: SubAgentResult,
    ) -> ToolResult:
        """Process subagent result into tool output for main agent."""

        # Format output for main agent's context
        output_text = f"""## Exploration Report

{raw_result.result}

---
_Subagent ID: `{raw_result.conversation_id}` (use with `resume` parameter to continue)_
_Turns: {raw_result.turns_used} | Tokens: {raw_result.total_tokens:,}_"""

        # Create function_call_output message
        output_message = self._create_function_call_output(
            conv_id=context.conv_id,
            call_id=context.call_id,
            function_output=output_text,
            metadata={
                "source": "task",
                "subagent_conversation_id": raw_result.conversation_id,
                "turns_used": raw_result.turns_used,
                "total_tokens": raw_result.total_tokens,
            },
        )

        return ToolResult(
            output_message=output_message,
            human_message=None,
            metadata=None,
        )

    def _create_function_call_output(
        self,
        conv_id: str,
        call_id: str,
        function_output: str,
        metadata: dict,
    ) -> MessageResponse:
        return MessageResponse(
            id=str(uuid.uuid4()),
            conversation_id=conv_id,
            sequence_number=0,
            type="function_call_output",
            role="tool",
            content=None,
            call_id=call_id,
            function_output=function_output,
            status="completed",
            metadata=metadata,
            created_at=int(time.time() * 1000),
            updated_at=int(time.time() * 1000),
        )
