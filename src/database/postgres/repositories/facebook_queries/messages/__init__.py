"""
Facebook Messages Query Module.

This module provides database operations for Facebook Messenger:
- conversations.py: Conversation CRUD and read state operations
- messages.py: Message CRUD and listing operations
"""

from .conversations import (
    get_conversation_by_participants,
    create_conversation,
    update_conversation_after_message,
    refresh_conversation_latest_message,
    update_conversation_ad_context,
    mark_page_messages_seen_by_user,
    mark_conversation_messages_as_seen,
    update_conversation_mark_as_read,
    get_conversation_with_details,
    get_conversations_with_details_batch,
    list_conversations_by_page_ids,
)
from .messages import (
    create_message,
    batch_create_messages,
    list_messages_by_conversation_id,
    list_messages_by_conversation_id_paginated,
)

__all__ = [
    # Conversation operations
    "get_conversation_by_participants",
    "create_conversation",
    "update_conversation_after_message",
    "refresh_conversation_latest_message",
    "update_conversation_ad_context",
    "mark_page_messages_seen_by_user",
    "mark_conversation_messages_as_seen",
    "update_conversation_mark_as_read",
    "get_conversation_with_details",
    "get_conversations_with_details_batch",
    "list_conversations_by_page_ids",
    # Message operations
    "create_message",
    "batch_create_messages",
    "list_messages_by_conversation_id",
    "list_messages_by_conversation_id_paginated",
]
