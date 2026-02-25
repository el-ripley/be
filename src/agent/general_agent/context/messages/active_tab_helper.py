from typing import Any, Dict, List, Optional

import asyncpg


async def build_active_tab_system_reminder_block(
    conn: asyncpg.Connection, active_tab: Optional[Dict[str, Any]]
) -> Optional[str]:
    """
    Build a <system-reminder> block describing the active tab.

    Returns:
        Formatted string to be used as content item text, or None if invalid.
    """
    if not isinstance(active_tab, dict):
        return None

    tab_type = active_tab.get("type")
    tab_id = active_tab.get("id")

    if not tab_type or not tab_id:
        return None

    lines: List[str] = ["<system-reminder>"]

    if tab_type == "conv_messages":
        lines.extend(
            [
                "The user is viewing **FB Inbox** (Messenger conversation).",
                f"- `facebook_conversation_message_id`: `{tab_id}`",
            ]
        )
    elif tab_type == "conv_comments":
        lines.extend(
            [
                "The user currently viewing **FB Comment Thread** on a post.",
                f"- `root_comment_id`: `{tab_id}` (this is the root comment of the thread)",
            ]
        )
    else:
        lines.extend(
            [
                "The user is viewing a Facebook conversation.",
                f"- Type: `{tab_type}`",
                f"- ID: `{tab_id}`",
            ]
        )

    lines.extend(
        [
            "",
            'When the user says "this conversation", "these comments", "here", "this" → they refer to this currently open Facebook inbox/comment thread and its related entities (page, page_scope_user, messages/comments).',
            "",
            "**IMPORTANT**: This context may or may not be relevant to your tasks. You should not respond to this context unless it is highly relevant to your task.",
            "</system-reminder>",
        ]
    )

    return "\n".join(lines)


def prepend_system_reminder_to_content(
    content_items: List[Dict[str, Any]], system_reminder: Optional[str]
) -> List[Dict[str, Any]]:
    """
    Prepend system-reminder block as the first item in content array if provided.
    """
    if not system_reminder:
        return content_items

    return [
        {"type": "input_text", "text": system_reminder},
        *content_items,
    ]


__all__ = [
    "build_active_tab_system_reminder_block",
    "prepend_system_reminder_to_content",
]
