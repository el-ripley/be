"""
Async MongoDB connection management with transaction support.

This module handles MongoDB connections using motor with async connection management,
providing async transaction context managers for high-performance database operations.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional, Tuple

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorClientSession,
    AsyncIOMotorDatabase,
)

from src.settings import settings
from src.utils.logger import get_logger

logger = get_logger()

# Global async MongoDB client
_async_mongo_client: Optional[AsyncIOMotorClient] = None
_client_lock = asyncio.Lock()


async def get_async_mongo_client() -> AsyncIOMotorClient:
    """
    Get or create the global async MongoDB client.

    Returns:
        AsyncIOMotorClient: The async MongoDB client instance
    """
    global _async_mongo_client

    if _async_mongo_client is None:
        async with _client_lock:
            if _async_mongo_client is None:
                try:
                    _async_mongo_client = AsyncIOMotorClient(
                        settings.mongodb_connection_string,
                        maxPoolSize=20,
                        minPoolSize=1,
                        retryWrites=True,
                        serverSelectionTimeoutMS=5000,
                        connectTimeoutMS=10000,
                    )
                    logger.info("Async MongoDB client created successfully")
                except Exception as e:
                    logger.error(f"Failed to create async MongoDB client: {e}")
                    raise

    return _async_mongo_client


async def close_async_mongo_connection():
    """Close the async MongoDB client connection."""
    global _async_mongo_client

    if _async_mongo_client:
        async with _client_lock:
            if _async_mongo_client:
                _async_mongo_client.close()
                _async_mongo_client = None
                logger.info("Async MongoDB client closed")


@asynccontextmanager
async def get_async_mongo_connection() -> AsyncGenerator[AsyncIOMotorDatabase, None]:
    """
    Get an async MongoDB database connection.

    Yields:
        AsyncIOMotorDatabase: Async database connection

    Example:
        async with get_async_mongo_connection() as db:
            users = await db.users.find({"active": True}).to_list(length=100)
    """
    client = await get_async_mongo_client()

    try:
        db = client[settings.mongodb_db_name]
        yield db
    except Exception as e:
        logger.error(f"Async MongoDB connection error: {e}")
        raise


@asynccontextmanager
async def async_mongo_transaction() -> (
    AsyncGenerator[Tuple[AsyncIOMotorDatabase, AsyncIOMotorClientSession], None]
):
    """
    Async MongoDB transaction context manager.

    Automatically handles transaction lifecycle:
    - Begins transaction
    - Commits on success
    - Aborts on exception
    - Ensures session is closed

    Yields:
        tuple: (database, session) for transaction operations

    Example:
        async with async_mongo_transaction() as (db, session):
            # Multiple operations in single transaction
            await db.users.insert_one(user_data, session=session)
            await db.profiles.insert_one(profile_data, session=session)
            # Auto commit on success, abort on exception
    """
    client = await get_async_mongo_client()

    async with await client.start_session() as session:
        try:
            async with session.start_transaction():
                db = client[settings.mongodb_db_name]
                yield db, session
                logger.debug("Async MongoDB transaction committed successfully")

        except Exception as e:
            # Abort is automatic with async context manager
            logger.error(f"Async MongoDB transaction aborted due to error: {e}")
            raise


async def check_async_mongo_connection() -> bool:
    """
    Check if async MongoDB connection is working.

    Returns:
        bool: True if connection successful, False otherwise
    """
    try:
        client = await get_async_mongo_client()
        # The ping command is cheap and does not require auth.
        await client.admin.command("ping")
        return True
    except Exception as e:
        logger.error(f"Async MongoDB connection check failed: {e}")
        return False


# Performance monitoring utilities
class AsyncMongoStats:
    """Track async MongoDB connection statistics."""

    @staticmethod
    async def get_client_stats() -> dict:
        """Get current MongoDB client statistics."""
        client = await get_async_mongo_client()
        try:
            # Get server info
            server_info = await client.admin.command("serverStatus")
            return {
                "connections_current": server_info.get("connections", {}).get(
                    "current", 0
                ),
                "connections_available": server_info.get("connections", {}).get(
                    "available", 0
                ),
                "uptime_seconds": server_info.get("uptime", 0),
                "version": server_info.get("version", "unknown"),
            }
        except Exception as e:
            logger.error(f"Failed to get MongoDB stats: {e}")
            return {"error": str(e)}

    @staticmethod
    async def log_client_stats():
        """Log current MongoDB statistics."""
        stats = await AsyncMongoStats.get_client_stats()
        logger.info(f"Async MongoDB Stats: {stats}")


# FastAPI startup/shutdown handlers
async def startup_async_mongo():
    """Initialize async MongoDB client on FastAPI startup."""
    try:
        await get_async_mongo_client()
        logger.info("Async MongoDB initialized successfully")

        # Test connection
        if await check_async_mongo_connection():
            logger.info("Async MongoDB connection test passed")
        else:
            logger.error("Async MongoDB connection test failed")

    except Exception as e:
        logger.error(f"Failed to initialize async MongoDB: {e}")
        raise


async def shutdown_async_mongo():
    """Close async MongoDB client on FastAPI shutdown."""
    try:
        await close_async_mongo_connection()
        logger.info("Async MongoDB shutdown completed")
    except Exception as e:
        logger.error(f"Error during async MongoDB shutdown: {e}")


# Collection utilities
class AsyncCollectionHelper:
    """Helper utilities for MongoDB collections."""

    @staticmethod
    async def ensure_indexes(
        db: AsyncIOMotorDatabase, collection_name: str, indexes: list
    ):
        """Ensure indexes exist on a collection."""
        try:
            collection = db[collection_name]
            for index in indexes:
                await collection.create_index(index)
            logger.info(f"Indexes ensured for collection: {collection_name}")
        except Exception as e:
            logger.error(f"Failed to create indexes for {collection_name}: {e}")
            raise

    @staticmethod
    async def get_collection_stats(
        db: AsyncIOMotorDatabase, collection_name: str
    ) -> dict:
        """Get statistics for a collection."""
        try:
            stats = await db.command("collStats", collection_name)
            return {
                "document_count": stats.get("count", 0),
                "size_bytes": stats.get("size", 0),
                "index_count": stats.get("nindexes", 0),
                "avg_doc_size": stats.get("avgObjSize", 0),
            }
        except Exception as e:
            logger.error(f"Failed to get stats for {collection_name}: {e}")
            return {"error": str(e)}


# Migration helper: Dual mode support
class MongoMode:
    """Helper to support async MongoDB operations."""

    @staticmethod
    async def is_async_available() -> bool:
        """Check if async MongoDB client is available."""
        return _async_mongo_client is not None

    @staticmethod
    async def get_connection_info() -> dict:
        """Get information about current MongoDB connection setup."""
        info = {
            "async_client_available": await MongoMode.is_async_available(),
            "async_client_stats": None,
        }

        if await MongoMode.is_async_available():
            info["async_client_stats"] = await AsyncMongoStats.get_client_stats()

        return info
