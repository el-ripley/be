"""Tool to ask user questions during agent execution (HITL).

This tool allows the agent to pause execution and wait for user input.
When this tool is called, the agent loop will pause and wait for the user
to provide answers before continuing.

TOOL_RESULT STRUCTURE (what agent sees after user answers):

function_call_output (output_message.function_output):
   "User has answered your questions: \"Question 1\"=\"Answer 1\", \"Question 2\"=\"Answer 2\". You can now continue with the user's answers in mind."

function_call_output (output_message.metadata):
   {
     "source": "ask_user_question",
     "user_selections": [
       {
         "question_index": 0,
         "question": "Question text",
         "header": "Header",
         "selected": {  // Object for single-select, array for multi-select
           "label": "Selected option label",
           "description": "Option description",
           "is_custom": false  // true if custom text input
         }
         // OR for multi-select:
         // "selected": [
         //   {"label": "Option 1", "description": "...", "is_custom": false},
         //   {"label": "Option 2", "description": "...", "is_custom": false}
         // ]
       }
     ]
   }
"""

import uuid
import time
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
Use this tool when you need to ask the user questions during execution. This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take.

Usage notes:
- Users will always be able to select "Other" to provide custom text input
- Use multiSelect: true to allow multiple answers to be selected for a question
- If you recommend a specific option, make that the first option in the list and add "(Recommended)" at the end of the label
"""


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


class AskUserQuestionTool(BaseTool):
    """Tool to ask user questions during agent execution."""

    @property
    def name(self) -> str:
        """Tool name for function calls."""
        return "ask_user_question"

    @property
    def definition(self) -> Dict[str, Any]:
        """OpenAI function tool definition based on Claude Code schema."""
        return {
            "type": "function",
            "name": "ask_user_question",
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "description": "Questions to ask the user (1-4 questions)",
                        "minItems": 1,
                        "maxItems": 4,
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "description": 'The complete question to ask the user. Should be clear, specific, and end with a question mark. Example: "Which library should we use for date formatting?" If multiSelect is true, phrase it accordingly, e.g. "Which features do you want to enable?"',
                                    "type": "string",
                                },
                                "header": {
                                    "description": 'Very short label displayed as a chip/tag (max 12 chars). Examples: "Auth method", "Library", "Approach".',
                                    "type": "string",
                                },
                                "options": {
                                    "description": "The available choices for this question. Must have 2-4 options. Each option should be a distinct, mutually exclusive choice (unless multiSelect is enabled). There should be no 'Other' option, that will be provided automatically.",
                                    "minItems": 2,
                                    "maxItems": 4,
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {
                                                "description": "The display text for this option that the user will see and select. Should be concise (1-5 words) and clearly describe the choice.",
                                                "type": "string",
                                            },
                                            "description": {
                                                "description": "Explanation of what this option means or what will happen if chosen. Useful for providing context about trade-offs or implications.",
                                                "type": "string",
                                            },
                                        },
                                        "required": ["label", "description"],
                                        "additionalProperties": False,
                                    },
                                },
                                "multiSelect": {
                                    "description": "Set to true to allow the user to select multiple options instead of just one. Use when choices are not mutually exclusive.",
                                    "default": False,
                                    "type": "boolean",
                                },
                            },
                            "required": [
                                "question",
                                "header",
                                "options",
                                "multiSelect",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "answers": {
                        "description": "User answers collected by the permission component",
                        "type": "object",
                        "propertyNames": {"type": "string"},
                        "additionalProperties": {"type": "string"},
                    },
                    "metadata": {
                        "description": "Optional metadata for tracking and analytics purposes. Not displayed to user.",
                        "type": "object",
                        "properties": {
                            "source": {
                                "description": 'Optional identifier for the source of this question (e.g., "remember" for /remember command). Used for analytics tracking.',
                                "type": "string",
                            }
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["questions"],
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
        Execute tool - this should never be called in normal flow.

        The AgentRunner will detect ask_user_question calls and pause before
        executing tools. This method is only called when resuming with user answers.
        """
        # This should not be called during normal execution
        # If it is, it means the tool was called after user provided answers
        # Return the answers that were already provided
        answers = arguments.get("answers", {})
        questions = arguments.get("questions", [])

        # Format the result content similar to Claude Code example
        answer_parts = []
        for idx, question in enumerate(questions):
            question_text = question.get("question", "")
            answer_key = str(idx)
            answer_text = answers.get(answer_key, "")
            if answer_text:
                answer_parts.append(f'"{question_text}"="{answer_text}"')

        if answer_parts:
            content = f"User has answered your questions: {', '.join(answer_parts)}. You can now continue with the user's answers in mind."
        else:
            content = "User has provided answers. You can now continue with the user's answers in mind."

        # Return plain text content (not JSON wrapped)
        return content

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Process raw result into ToolResult."""
        # raw_result is now plain text content (not JSON)
        function_output = raw_result

        # Extract user answers from context to add to metadata
        answers = context.arguments.get("answers", {})
        questions = context.arguments.get("questions", [])

        # Build metadata with selected options for FE rendering
        # Create a structured format of user selections
        user_selections = []
        for idx, question in enumerate(questions):
            answer_key = str(idx)
            answer_text = answers.get(answer_key, "")
            if answer_text:
                options = question.get("options", [])
                multi_select = question.get("multiSelect", False)

                # Handle multi-select: answers might be comma-separated
                if multi_select and "," in answer_text:
                    answer_labels = [a.strip() for a in answer_text.split(",")]
                else:
                    answer_labels = [answer_text]

                # Find which options were selected
                selected_options = []
                for answer_label in answer_labels:
                    selected_option = None
                    for option in options:
                        if option.get("label") == answer_label:
                            selected_option = {
                                "label": option.get("label"),
                                "description": option.get("description"),
                            }
                            break

                    # If not found in options, it might be custom text ("Other" option)
                    if not selected_option:
                        selected_option = {
                            "label": answer_label,
                            "description": None,
                            "is_custom": True,
                        }

                    selected_options.append(selected_option)

                # For single-select, use the first (and only) option
                # For multi-select, include all selected options
                user_selections.append(
                    {
                        "question_index": idx,
                        "question": question.get("question", ""),
                        "header": question.get("header", ""),
                        "selected": (
                            selected_options[0]
                            if not multi_select
                            else selected_options
                        ),
                    }
                )

        # Use Dict[str, Any] to allow custom fields (user_selections) in metadata
        metadata: Dict[str, Any] = {
            "source": "ask_user_question",
            "user_selections": user_selections,  # This will be in metadata for FE
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
