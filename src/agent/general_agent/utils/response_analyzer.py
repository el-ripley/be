"""Response analyzer for LLM responses."""

from typing import Any, Dict, Optional


class ResponseAnalyzer:
    """Analyze LLM response to determine next action."""

    @staticmethod
    def is_final(response_dict: Dict[str, Any]) -> bool:
        """Check if response contains tool calls."""
        output_items = response_dict.get("output", [])
        for item in output_items:
            if item.get("type") == "function_call":
                return False
        return True

    @staticmethod
    def has_ask_user_question(response_dict: Dict[str, Any]) -> bool:
        """Check if response contains ask_user_question tool call."""
        output_items = response_dict.get("output", [])
        for item in output_items:
            if item.get("type") == "function_call":
                # Check if it's ask_user_question tool
                name = item.get("name")
                if name == "ask_user_question":
                    return True
        return False

    @staticmethod
    def extract_final_content(response_dict: Dict[str, Any]) -> Optional[str]:
        """Extract final text content from response.

        Args:
            response_dict: Response dictionary from OpenAI API

        Returns:
            Extracted text content or None if not found
        """
        output_items = response_dict.get("output", [])
        for item in output_items:
            if item.get("type") == "message":
                content = item.get("content", [])
                if isinstance(content, list):
                    # Extract text from content items
                    # Support both "text" and "output_text" types (Response API uses "output_text")
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict):
                            part_type = part.get("type")
                            if part_type == "text":
                                text_parts.append(part.get("text", ""))
                            elif part_type == "output_text":  # Response API format
                                text_parts.append(part.get("text", ""))
                    return "".join(text_parts)
                elif isinstance(content, str):
                    return content
        return None
