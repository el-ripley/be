"""Tool to create and manage a structured task list for the current session.

This tool allows the agent to track progress, organize complex tasks, and give
the user visibility into the work being done. The agent tracks todo state through
conversation history, and the frontend detects tool calls to render the UI.
"""

import uuid
import time
import re
from typing import Any, Dict, List

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """Use this tool to create and manage a structured task list for your current session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.

It also helps the user understand the progress of the task and overall progress of their requests.

## When to Use This Tool

Use this tool proactively in these scenarios:

1. Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
2. Non-trivial and complex tasks - Tasks that require careful planning or multiple operations
3. User explicitly requests todo list - When the user directly asks you to use the todo list
4. User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
5. After receiving new instructions - Immediately capture user requirements as todos
6. When you start working on a task - Mark it as in_progress BEFORE beginning work. Ideally you should only have one todo as in_progress at a time
7. After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation

## When NOT to Use This Tool

Skip using this tool when:
1. There is only a single, straightforward task
2. The task is trivial and tracking it provides no organizational benefit
3. The task can be completed in less than 3 trivial steps
4. The task is purely conversational or informational

NOTE that you should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.

## Task States and Management

1. **Task States**: Use these states to track progress:
   - pending: Task not yet started
   - in_progress: Currently working on (limit to ONE task at a time)
   - completed: Task finished successfully

   **IMPORTANT**: Task descriptions must have two forms:
   - content: The imperative form describing what needs to be done (e.g., "Sync posts", "Fetch comments")
   - activeForm: The present continuous form shown during execution (e.g., "Syncing posts", "Fetching comments")

2. **Task Management**:
   - Update task status in real-time as you work
   - Mark tasks complete IMMEDIATELY after finishing (don't batch completions)
   - Exactly ONE task must be in_progress at any time (not less, not more)
   - Complete current tasks before starting new ones
   - Remove tasks that are no longer relevant from the list entirely

3. **Task Completion Requirements**:
   - ONLY mark a task as completed when you have FULLY accomplished it
   - If you encounter errors, blockers, or cannot finish, keep the task as in_progress
   - When blocked, create a new task describing what needs to be resolved

4. **Task Breakdown**:
   - Create specific, actionable items
   - Break complex tasks into smaller, manageable steps
   - Use clear, descriptive task names
   - Always provide both forms:
     - content: "Fetch conversation details"
     - activeForm: "Fetching conversation details"

When in doubt, use this tool. Being proactive with task management demonstrates attentiveness and ensures you complete all requirements successfully."""


def _sanitize_string(text: str) -> str:
    """
    Sanitize string by removing null bytes and other problematic control characters.

    PostgreSQL doesn't allow null bytes (\u0000) in text fields.
    Also removes other control characters that might cause issues.
    """
    if not isinstance(text, str):
        return text

    # Remove null bytes (PostgreSQL doesn't allow these)
    text = text.replace("\u0000", "")

    # Remove other problematic control characters (but keep common ones like \n, \t, \r)
    # Keep: \n (0x0A), \r (0x0D), \t (0x09)
    # Remove: \u0000-\u0008, \u000B-\u000C, \u000E-\u001F
    text = re.sub(r"[\u0000-\u0008\u000B-\u000C\u000E-\u001F]", "", text)

    return text


def _validate_and_sanitize_todos(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Validate and sanitize todos list.

    Raises ValueError with descriptive message if validation fails.
    """
    if not isinstance(todos, list):
        raise ValueError("todos must be a list")

    if len(todos) == 0:
        raise ValueError("todos list cannot be empty")

    sanitized = []
    for idx, todo in enumerate(todos):
        if not isinstance(todo, dict):
            raise ValueError(f"Todo at index {idx} must be an object")

        # Check required fields
        if "content" not in todo:
            raise ValueError(f"Todo at index {idx} is missing required field 'content'")
        if "status" not in todo:
            raise ValueError(f"Todo at index {idx} is missing required field 'status'")
        if "activeForm" not in todo:
            raise ValueError(
                f"Todo at index {idx} is missing required field 'activeForm'"
            )

        # Validate status
        if todo["status"] not in ["pending", "in_progress", "completed"]:
            raise ValueError(
                f"Todo at index {idx} has invalid status '{todo['status']}'. "
                "Must be one of: pending, in_progress, completed"
            )

        # Validate and sanitize string fields
        content = todo.get("content", "")
        active_form = todo.get("activeForm", "")

        if not isinstance(content, str) or len(content.strip()) == 0:
            raise ValueError(
                f"Todo at index {idx} has invalid or empty 'content' field"
            )
        if not isinstance(active_form, str) or len(active_form.strip()) == 0:
            raise ValueError(
                f"Todo at index {idx} has invalid or empty 'activeForm' field"
            )

        # Sanitize strings to remove problematic characters
        sanitized_content = _sanitize_string(content)
        sanitized_active_form = _sanitize_string(active_form)

        if len(sanitized_content.strip()) == 0:
            raise ValueError(
                f"Todo at index {idx} 'content' field contains only invalid characters "
                "(null bytes or control characters). Please provide valid text."
            )
        if len(sanitized_active_form.strip()) == 0:
            raise ValueError(
                f"Todo at index {idx} 'activeForm' field contains only invalid characters "
                "(null bytes or control characters). Please provide valid text."
            )

        # Create sanitized todo
        sanitized_todo = {
            "content": sanitized_content,
            "status": todo["status"],
            "activeForm": sanitized_active_form,
        }
        sanitized.append(sanitized_todo)

    return sanitized


def _create_function_call_output(
    conv_id: str,
    call_id: str,
    function_output: Any,
    metadata: Any = None,
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
        metadata=metadata,
        created_at=current_time,
        updated_at=current_time,
    )


class TodoWriteTool(BaseTool):
    """Tool to create and manage a structured task list for the current session."""

    @property
    def name(self) -> str:
        """Tool name for function calls."""
        return "todo_write"

    @property
    def definition(self) -> Dict[str, Any]:
        """OpenAI function tool definition."""
        return {
            "type": "function",
            "name": "todo_write",
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "description": "The updated todo list",
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "minLength": 1,
                                    "description": "The imperative form describing what needs to be done (e.g., 'Sync posts', 'Fetch comments')",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "Current status of the task",
                                },
                                "activeForm": {
                                    "type": "string",
                                    "minLength": 1,
                                    "description": "The present continuous form shown during execution (e.g., 'Syncing posts', 'Fetching comments')",
                                },
                            },
                            "required": ["content", "status", "activeForm"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["todos"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """
        Execute tool - validates and sanitizes todos, returns confirmation or error message.

        The todos are validated and sanitized to remove problematic characters (like null bytes)
        that PostgreSQL cannot store. If validation fails, raises ValueError with descriptive
        message so the agent can retry with corrected input.
        """
        todos = arguments.get("todos", [])

        try:
            # Validate and sanitize todos
            sanitized_todos = _validate_and_sanitize_todos(todos)

            # Store sanitized todos in context for use in process_result
            # We'll use a simple approach: store in context.arguments
            context.arguments["todos"] = sanitized_todos

            # Return static confirmation message
            return "Todos have been modified successfully. Ensure that you continue to use the todo list to track your progress. Please proceed with the current tasks if applicable"

        except ValueError as e:
            # Validation failed - return error message for agent to see and retry
            error_msg = (
                f"Error validating todos: {str(e)}. "
                "Please check that all todo items have valid 'content', 'status', and 'activeForm' fields. "
                "Make sure text fields do not contain null bytes (\\u0000) or other invalid control characters. "
                "Please retry with corrected todo list."
            )
            logger.warning(f"todo_write validation failed: {str(e)}")
            raise ValueError(error_msg)

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Process raw result into ToolResult."""
        # raw_result is the static confirmation message or error message
        function_output = raw_result

        # Extract todos from arguments (use sanitized version if available)
        # If validation failed, this might be the original unsanitized list
        todos = context.arguments.get("todos", [])

        # If we have sanitized todos (stored during execute), use those
        # Otherwise, try to sanitize here as a fallback
        try:
            if todos:
                sanitized_todos = _validate_and_sanitize_todos(todos)
                todos = sanitized_todos
        except ValueError:
            # If sanitization fails here, log but don't fail - we already handled error in execute
            logger.warning("Failed to sanitize todos in process_result, using original")
            # Try basic sanitization at least
            todos = [
                {
                    "content": _sanitize_string(todo.get("content", "")),
                    "status": todo.get("status", "pending"),
                    "activeForm": _sanitize_string(todo.get("activeForm", "")),
                }
                for todo in todos
                if isinstance(todo, dict)
            ]

        # Build metadata with todos for FE rendering
        metadata: Dict[str, Any] = {
            "source": "todo_write",
            "todos": todos,  # This will be in metadata for FE to detect and render
        }

        output_message = _create_function_call_output(
            conv_id=context.conv_id,
            call_id=context.call_id,
            function_output=function_output,
            metadata=metadata,
        )

        return ToolResult(
            output_message=output_message,
            human_message=None,
            metadata=None,
        )
