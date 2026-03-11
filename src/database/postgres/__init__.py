"""
Async database abstraction layer for high-performance SQL operations.

This module provides a clean async abstraction over raw SQL operations while
maintaining transaction consistency and connection management for FastAPI.
"""

from .connection import (
    async_db_transaction,
    check_async_database_connection,
    close_async_connection_pool,
    get_async_connection,
    get_async_connection_pool,
    shutdown_async_database,
    startup_async_database,
)

# Import entities for database operations
from .entities import (  # User entities; Facebook entities; Comments domain entities (Posts + Comments); Messages domain entities
    Comment,
    FacebookAppScopeUser,
    FacebookPageAdmin,
    FacebookPageScopeUser,
    FanPage,
    Message,
    Post,
    Role,
    User,
)
from .executor import (
    bulk_insert_async,
    execute_async_command,
    execute_async_many,
    execute_async_query,
    execute_async_returning,
    execute_async_scalar,
    execute_async_single,
)
from .utils import generate_uuid, get_current_timestamp

# Note: Schema imports removed as all database schemas were unused

__all__ = [
    # Connection management
    "get_async_connection",
    "get_async_connection_pool",
    "async_db_transaction",
    "close_async_connection_pool",
    "startup_async_database",
    "shutdown_async_database",
    "check_async_database_connection",
    # Query execution
    "execute_async_query",
    "execute_async_single",
    "execute_async_command",
    "execute_async_many",
    "execute_async_returning",
    "execute_async_scalar",
    "bulk_insert_async",
    # Utilities
    "generate_uuid",
    "get_current_timestamp",
    # Database entities
    "Role",
    "User",
    "FacebookAppScopeUser",
    "FanPage",
    "FacebookPageAdmin",
    "FacebookPageScopeUser",
    "Post",
    "Comment",
    "Message",
    # Note: Schema exports removed - all database schemas were unused
]
