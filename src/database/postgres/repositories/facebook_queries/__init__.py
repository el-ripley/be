"""
Facebook-related SQL query functions.

This module provides access to all Facebook-related database operations.
Functions are organized into sub-modules for better maintainability:

- pages: Facebook page, page scope users, app scope users, and page admin operations
- messages/: Conversation and message operations
  - conversations.py: Conversation CRUD and read state management
  - messages.py: Message CRUD and listing
- comments/: Post and comment operations
- Note: Media assets are now handled by media_assets_queries (unified table)
"""

from .comments import *
from .inbox_sync_states import *
from .messages import *

# Import all functions from sub-modules for backward compatibility
from .pages import *
from .reactions import *
