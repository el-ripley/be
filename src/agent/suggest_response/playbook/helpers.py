"""Helper functions for playbook retrieval."""

from datetime import datetime, timezone
from typing import Any, Dict, List

from src.agent.suggest_response.playbook.tools.system_prompt import (
    build_playbook_system_prompt,
)


def input_items_for_api(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert our message list to API input format (matches suggest_response temp context)."""
    result: List[Dict[str, Any]] = []
    for m in messages:
        if m.get("type") == "function_call":
            result.append(
                {
                    "type": "function_call",
                    "call_id": m["call_id"],
                    "name": m["name"],
                    "arguments": m.get("arguments", "{}"),
                }
            )
        elif m.get("type") == "function_call_output":
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": m["call_id"],
                    "output": m.get("output", "{}"),
                }
            )
        else:
            result.append(
                {
                    "type": "message",
                    "role": m["role"],
                    "content": m["content"],
                }
            )
    return result


def format_playbooks_as_system_reminder(playbooks: List[Dict[str, Any]]) -> str:
    """Format matched playbooks as a single system-reminder block."""
    if not playbooks:
        return ""
    parts = [
        "<system-reminder>",
        "## Situation-Specific Guidelines",
        "",
        "Below are response guidelines relevant to the current conversation. Follow them where applicable when composing your reply.",
        "",
    ]
    for p in playbooks:
        title = p.get("title", "Untitled")
        situation = p.get("situation", "")
        content = p.get("content", "")
        parts.append(f"### {title}")
        if situation:
            parts.append(f"**Applies when:** {situation}")
        parts.append(content.strip())
        parts.append("")
    parts.append("</system-reminder>")
    return "\n".join(parts)


def content_to_text(content: Any) -> str:
    """Extract plain text from message content (string or array-of-objects)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("text")
        )
    return str(content) if content else ""


def build_initial_input_messages(
    input_messages: List[Dict[str, Any]],
    page_memory: str = "",
    user_memory: str = "",
) -> List[Dict[str, Any]]:
    """Build initial LLM input: system + non-system conversation messages.

    Args:
        input_messages: Full context messages from suggest_response (system + user/assistant).
        page_memory: Rendered page memory/policy text (injected into playbook system prompt).
        user_memory: Rendered user memory text (injected into playbook system prompt).

    Returns:
        Messages list with playbook selection system prompt + conversation turns.
    """
    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    system_prompt = build_playbook_system_prompt(
        current_time=current_time,
        page_memory=page_memory,
        user_memory=user_memory,
    )
    result: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in input_messages:
        if msg.get("role") == "system":
            continue
        content = msg.get("content")
        text = content_to_text(content)
        if text:
            result.append({"role": msg["role"], "content": text})
    return result
