"""Shared normalizer for function_call_output to API format (input_text + text)."""

import json
from typing import Any, List


def content_to_text(content: Any) -> str:
    """Convert content to text for content block (API expects type+text)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def normalize_function_output_to_api_format(output: Any) -> List[dict]:
    """
    Convert any function_output to API format: array of {type: 'input_text', text: str}.
    When output is an array, API expects content block types: input_text, input_image,
    input_file, scoped_content (not output_text). Used when building context and when
    injecting iteration warnings.
    """
    if output is None:
        return [{"type": "input_text", "text": ""}]
    if isinstance(output, list):
        result: List[dict] = []
        for item in output:
            if isinstance(item, dict):
                if item.get("type") == "input_text" and "text" in item:
                    result.append({"type": "input_text", "text": item["text"]})
                elif item.get("type") == "output_text" and "text" in item:
                    result.append({"type": "input_text", "text": item["text"]})
                elif "content" in item or "text" in item:
                    text = item.get("text", content_to_text(item.get("content")))
                    result.append({"type": "input_text", "text": text})
                else:
                    result.append({"type": "input_text", "text": content_to_text(item)})
            else:
                result.append({"type": "input_text", "text": content_to_text(item)})
        return result if result else [{"type": "input_text", "text": ""}]
    return [{"type": "input_text", "text": content_to_text(output)}]
