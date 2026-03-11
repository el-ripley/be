"""
Facebook Messages Service Module.

This module provides services for handling Facebook Messenger events:
- MessageWebhookHandler: Main orchestrator for webhook events
- MessageAPIHandler: Handles API requests for messages
- MessageReadService: Read operations for messages and conversations

Note: 
- ConversationMessageHistorySync (formerly FacebookHistorySync) is in src.services.facebook.messages.sync.message_history_sync
- ConversationSyncService (formerly ConversationManager) is in src.services.facebook.messages.sync.conversation_sync_service
"""

from ._internal.attachment_parser import (
    AttachmentParser,
    build_entry_point,
    merge_entry_point,
    parse_attachments,
)

# Internal services (exported for backward compatibility and dependency injection)
from ._internal.message_processor import MessageProcessor
from ._internal.read_receipt_processor import ReadReceiptProcessor
from ._internal.socket_emitter import SocketEmitter
from .api_handler import MessageAPIHandler

# Public API exports
from .message_read_service import MessageReadService
from .webhook_handler import MessageWebhookHandler

__all__ = [
    # Public API
    "MessageReadService",
    "MessageWebhookHandler",
    "MessageAPIHandler",
    # Internal services (for dependency injection)
    "MessageProcessor",
    "ReadReceiptProcessor",
    "SocketEmitter",
    "AttachmentParser",
    "parse_attachments",
    "build_entry_point",
    "merge_entry_point",
]
