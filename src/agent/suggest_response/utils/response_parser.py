"""Response parser for Suggest Response LLM outputs."""

import json
from typing import Any, Dict, List, Optional, Tuple

from src.agent.suggest_response.schemas import (
    CommentSuggestion,
    MessageSuggestion,
    SuggestResponseOutput,
)
from src.utils.logger import get_logger

logger = get_logger()

TERMINAL_TOOLS = frozenset({"generate_suggestions", "complete_task"})


class SuggestResponseParser:
    """Parse and validate LLM tool call responses for suggest response."""

    def parse_tool_call_response(
        self,
        response_dict: Dict[str, Any],
        conversation_type: str,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        Parse LLM tool call response and extract suggestions.

        Args:
            response_dict: Dict containing response with tool calls
            conversation_type: 'messages' or 'comments'

        Returns:
            Tuple of (suggestions_list, output_text).
        """
        if not isinstance(response_dict, dict):
            raise ValueError(
                f"Expected dict from llm_call.create(), got {type(response_dict).__name__}"
            )

        # Extract terminal tool call (generate_suggestions or complete_task)
        output_items = response_dict.get("output", [])
        tool_call_item = None
        terminal_name = None

        for item in output_items:
            if item.get("type") == "function_call":
                name = item.get("name")
                if name in TERMINAL_TOOLS:
                    tool_call_item = item
                    terminal_name = name
                    break

        if not tool_call_item or terminal_name is None:
            raise ValueError(
                "No terminal tool call (generate_suggestions or complete_task) found in response."
            )

        # Parse tool call arguments
        arguments_str = tool_call_item.get("arguments", "{}")
        try:
            arguments = json.loads(arguments_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse tool call arguments: {str(e)}")

        if not isinstance(arguments, dict):
            raise ValueError("Tool call arguments must be a dict")

        # Handle complete_task
        if terminal_name == "complete_task":
            output_text = json.dumps(arguments)
            return [], output_text

        # Handle generate_suggestions
        try:
            suggestions_output = SuggestResponseOutput(**arguments)
        except Exception as e:
            logger.error(
                f"Failed to validate tool call arguments: {str(e)}, arguments: {arguments}"
            )
            raise ValueError(f"Invalid tool call arguments format: {str(e)}")

        # Convert suggestions to dict format (intermediate format with media_ids)
        # Runner will resolve media_ids → URLs before downstream delivery
        suggestions_list = []
        for suggestion in suggestions_output.suggestions:
            if isinstance(suggestion, MessageSuggestion):
                suggestions_list.append(
                    {
                        "message": suggestion.message,
                        "media_ids": suggestion.media_ids,
                        "video_url": suggestion.video_url,
                        "reply_to_ref": suggestion.reply_to_ref,
                    }
                )
            elif isinstance(suggestion, CommentSuggestion):
                suggestions_list.append(
                    {
                        "message": suggestion.message,
                        "attachment_media_id": suggestion.attachment_media_id,
                    }
                )

        output_text = json.dumps(arguments)
        return suggestions_list, output_text
