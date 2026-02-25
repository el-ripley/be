"""
Async MongoDB database abstraction layer.

This module provides async MongoDB-specific operations while maintaining
high performance for document storage and retrieval.
"""

from .connection import (
    get_async_mongo_connection,
    async_mongo_transaction,
    close_async_mongo_connection,
    startup_async_mongo,
    shutdown_async_mongo,
    check_async_mongo_connection,
    AsyncMongoStats,
    AsyncCollectionHelper,
)

from .executor import (
    insert_one_async,
    insert_many_async,
    find_one_async,
    find_many_async,
    update_one_async,
    update_many_async,
    delete_one_async,
    delete_many_async,
    count_documents_async,
    aggregate_async,
    find_by_id_async,
    upsert_async,
)

__all__ = [
    # Connection management
    "get_async_mongo_connection",
    "async_mongo_transaction",
    "close_async_mongo_connection",
    "startup_async_mongo",
    "shutdown_async_mongo",
    "check_async_mongo_connection",
    "AsyncMongoStats",
    "AsyncCollectionHelper",
    # Query execution
    "insert_one_async",
    "insert_many_async",
    "find_one_async",
    "find_many_async",
    "update_one_async",
    "update_many_async",
    "delete_one_async",
    "delete_many_async",
    "count_documents_async",
    "aggregate_async",
    "find_by_id_async",
    "upsert_async",
]
