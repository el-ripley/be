"""
Async database connection management with connection pooling and transaction support.

This module handles database connections using asyncpg with async connection pooling,
providing async transaction context managers for high-performance database operations.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import asyncpg

from src.settings import settings
from src.utils.logger import get_logger

logger = get_logger()

# Global async connection pool (main app user - full access)
_async_connection_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()

# Agent reader connection pool (RLS-restricted for AI agent SELECT queries)
_agent_reader_pool: Optional[asyncpg.Pool] = None
_agent_reader_pool_lock = asyncio.Lock()

# Agent writer connection pool (RLS-restricted for AI agent INSERT/UPDATE/DELETE queries)
_agent_writer_pool: Optional[asyncpg.Pool] = None
_agent_writer_pool_lock = asyncio.Lock()

# Suggest Response reader connection pool (conversation-scoped, minimal SELECT)
_suggest_response_reader_pool: Optional[asyncpg.Pool] = None
_suggest_response_reader_pool_lock = asyncio.Lock()

# Suggest Response writer connection pool (conversation-scoped, minimal INSERT/UPDATE)
_suggest_response_writer_pool: Optional[asyncpg.Pool] = None
_suggest_response_writer_pool_lock = asyncio.Lock()


async def get_async_connection_pool() -> asyncpg.Pool:
    """
    Get or create the global async connection pool.

    Returns:
        asyncpg.Pool: The async connection pool instance
    """
    global _async_connection_pool

    if _async_connection_pool is None:
        async with _pool_lock:
            if _async_connection_pool is None:
                try:
                    _async_connection_pool = await asyncpg.create_pool(
                        host=settings.postgres_host,
                        port=settings.postgres_port,
                        database=settings.postgres_db_name,
                        user=settings.postgres_user,
                        password=settings.postgres_password,
                        min_size=10,  # Minimum connections (increased for better concurrent sync performance)
                        max_size=100,  # Maximum connections (increased to support higher concurrency for sync operations)
                        command_timeout=30,
                        server_settings={
                            "application_name": "elripley_fastapi_async",
                        },
                    )
                    logger.info("Async database connection pool created successfully")
                except Exception as e:
                    logger.error(f"Failed to create async connection pool: {e}")
                    raise

    return _async_connection_pool


async def close_async_connection_pool():
    """Close the async connection pool."""
    global _async_connection_pool

    if _async_connection_pool:
        async with _pool_lock:
            if _async_connection_pool:
                await _async_connection_pool.close()
                _async_connection_pool = None
                logger.info("Async database connection pool closed")


# ================================================================
# AGENT READER CONNECTION POOL (RLS-restricted for SELECT)
# ================================================================


async def get_agent_reader_connection_pool() -> asyncpg.Pool:
    """
    Get or create the agent reader connection pool.
    This pool uses the agent_reader role with RLS restrictions (SELECT only).

    Returns:
        asyncpg.Pool: The agent reader connection pool instance
    """
    global _agent_reader_pool

    if _agent_reader_pool is None:
        async with _agent_reader_pool_lock:
            if _agent_reader_pool is None:
                try:
                    _agent_reader_pool = await asyncpg.create_pool(
                        host=settings.postgres_host,
                        port=settings.postgres_port,
                        database=settings.postgres_db_name,
                        user=settings.postgres_agent_reader_user,
                        password=settings.postgres_agent_reader_password,
                        min_size=5,  # Smaller pool for agent queries
                        max_size=20,  # Limited concurrent agent queries
                        command_timeout=10,  # Shorter timeout for agent queries
                        server_settings={
                            "application_name": "elripley_agent_reader",
                        },
                    )
                    logger.info("Agent reader connection pool created successfully")
                except Exception as e:
                    logger.error(f"Failed to create agent reader connection pool: {e}")
                    raise

    return _agent_reader_pool


async def close_agent_reader_connection_pool():
    """Close the agent reader connection pool."""
    global _agent_reader_pool

    if _agent_reader_pool:
        async with _agent_reader_pool_lock:
            if _agent_reader_pool:
                await _agent_reader_pool.close()
                _agent_reader_pool = None
                logger.info("Agent reader connection pool closed")


@asynccontextmanager
async def get_agent_reader_connection(
    user_id: str,
) -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Get an agent reader connection with RLS context set.

    This sets the app.current_user_id session variable so RLS policies
    can filter data to only what the user has access to.

    Args:
        user_id: The user ID to set for RLS filtering

    Yields:
        asyncpg.Connection: Agent reader connection with RLS context

    Example:
        async with get_agent_reader_connection(user_id="user-123") as conn:
            # This query will only return pages the user has access to
            result = await conn.fetch("SELECT * FROM fan_pages")
    """
    pool = await get_agent_reader_connection_pool()

    async with pool.acquire() as conn:
        try:
            # Set the user context for RLS policies
            # Using SET so it affects this session
            # Note: SET doesn't support parameterized queries ($1), so we use format
            # user_id is validated UUID string, escape single quotes for safety
            escaped_user_id = user_id.replace("'", "''")
            await conn.execute(f"SET app.current_user_id = '{escaped_user_id}'")
            yield conn
        except Exception as e:
            logger.error(f"Agent reader connection error: {e}")
            raise


# ================================================================
# AGENT WRITER CONNECTION POOL (RLS-restricted for INSERT/UPDATE/DELETE)
# ================================================================


async def get_agent_writer_connection_pool() -> asyncpg.Pool:
    """
    Get or create the agent writer connection pool.
    This pool uses the agent_writer role with RLS restrictions (INSERT/UPDATE/DELETE).

    Returns:
        asyncpg.Pool: The agent writer connection pool instance
    """
    global _agent_writer_pool

    if _agent_writer_pool is None:
        async with _agent_writer_pool_lock:
            if _agent_writer_pool is None:
                try:
                    _agent_writer_pool = await asyncpg.create_pool(
                        host=settings.postgres_host,
                        port=settings.postgres_port,
                        database=settings.postgres_db_name,
                        user=settings.postgres_agent_writer_user,
                        password=settings.postgres_agent_writer_password,
                        min_size=3,  # Smaller pool for write operations
                        max_size=10,  # Limited concurrent write operations
                        command_timeout=30,  # Longer timeout for write operations
                        server_settings={
                            "application_name": "elripley_agent_writer",
                        },
                    )
                    logger.info("Agent writer connection pool created successfully")
                except Exception as e:
                    logger.error(f"Failed to create agent writer connection pool: {e}")
                    raise

    return _agent_writer_pool


async def close_agent_writer_connection_pool():
    """Close the agent writer connection pool."""
    global _agent_writer_pool

    if _agent_writer_pool:
        async with _agent_writer_pool_lock:
            if _agent_writer_pool:
                await _agent_writer_pool.close()
                _agent_writer_pool = None
                logger.info("Agent writer connection pool closed")


@asynccontextmanager
async def get_agent_writer_transaction(
    user_id: str,
) -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Get an agent writer connection wrapped in a transaction with RLS context set.

    This sets the app.current_user_id session variable and wraps operations
    in a transaction for atomicity. All statements are executed in a single
    transaction that commits on success or rolls back on error.

    Args:
        user_id: The user ID to set for RLS filtering

    Yields:
        asyncpg.Connection: Agent writer connection with transaction and RLS context

    Example:
        async with get_agent_writer_transaction(user_id="user-123") as conn:
            # All statements execute in a transaction
            await conn.execute("UPDATE page_memory SET is_active = FALSE WHERE id = $1", old_id)
            await conn.execute("INSERT INTO page_memory (...) VALUES (...)")
            # Auto commit on success, rollback on exception
    """
    pool = await get_agent_writer_connection_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # Set the user context for RLS policies
                # Using SET LOCAL so it only affects this transaction
                # Note: SET doesn't support parameterized queries ($1), so we use format
                # user_id is validated UUID string, escape single quotes for safety
                escaped_user_id = user_id.replace("'", "''")
                await conn.execute(
                    f"SET LOCAL app.current_user_id = '{escaped_user_id}'"
                )
                yield conn
            except Exception as e:
                logger.error(f"Agent writer transaction error: {e}")
                raise


# ================================================================
# SUGGEST RESPONSE READER CONNECTION POOL (conversation-scoped)
# ================================================================


async def get_suggest_response_reader_connection_pool() -> asyncpg.Pool:
    """
    Get or create the suggest response reader connection pool.
    This pool uses the suggest_response_reader role with conversation-scoped RLS.

    Returns:
        asyncpg.Pool: The suggest response reader connection pool instance
    """
    global _suggest_response_reader_pool

    if _suggest_response_reader_pool is None:
        async with _suggest_response_reader_pool_lock:
            if _suggest_response_reader_pool is None:
                try:
                    _suggest_response_reader_pool = await asyncpg.create_pool(
                        host=settings.postgres_host,
                        port=settings.postgres_port,
                        database=settings.postgres_db_name,
                        user=settings.postgres_suggest_response_reader_user,
                        password=settings.postgres_suggest_response_reader_password,
                        min_size=3,  # Smaller pool for suggest response queries
                        max_size=15,  # Limited concurrent queries
                        command_timeout=10,  # Short timeout
                        server_settings={
                            "application_name": "elripley_suggest_response_reader",
                        },
                    )
                    logger.info(
                        "Suggest response reader connection pool created successfully"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to create suggest response reader connection pool: {e}"
                    )
                    raise

    return _suggest_response_reader_pool


async def close_suggest_response_reader_connection_pool():
    """Close the suggest response reader connection pool."""
    global _suggest_response_reader_pool

    if _suggest_response_reader_pool:
        async with _suggest_response_reader_pool_lock:
            if _suggest_response_reader_pool:
                await _suggest_response_reader_pool.close()
                _suggest_response_reader_pool = None
                logger.info("Suggest response reader connection pool closed")


@asynccontextmanager
async def get_suggest_response_reader_connection(
    user_id: str,
    conversation_type: str,
    conversation_id: str,
    fan_page_id: str,
    page_scope_user_id: Optional[str] = None,
) -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Get a suggest response reader connection with conversation-scoped RLS context.

    Sets multiple session variables for conversation-scoped RLS policies:
    - app.current_user_id: Owner user ID
    - app.current_conversation_type: 'messages' or 'comments'
    - app.current_conversation_id: The conversation ID
    - app.current_fan_page_id: The fan page ID
    - app.current_page_scope_user_id: PSID (only for messages)

    Args:
        user_id: Owner user ID for RLS filtering
        conversation_type: 'messages' or 'comments'
        conversation_id: The conversation ID being served
        fan_page_id: The fan page ID
        page_scope_user_id: PSID (required for messages, optional for comments)

    Yields:
        asyncpg.Connection: Suggest response reader connection with RLS context
    """
    pool = await get_suggest_response_reader_connection_pool()

    async with pool.acquire() as conn:
        try:
            # Set all RLS context variables
            escaped_user_id = user_id.replace("'", "''")
            escaped_conv_type = conversation_type.replace("'", "''")
            escaped_conv_id = conversation_id.replace("'", "''")
            escaped_fan_page_id = fan_page_id.replace("'", "''")

            await conn.execute(f"SET app.current_user_id = '{escaped_user_id}'")
            await conn.execute(
                f"SET app.current_conversation_type = '{escaped_conv_type}'"
            )
            await conn.execute(f"SET app.current_conversation_id = '{escaped_conv_id}'")
            await conn.execute(f"SET app.current_fan_page_id = '{escaped_fan_page_id}'")

            if page_scope_user_id:
                escaped_psid = page_scope_user_id.replace("'", "''")
                await conn.execute(
                    f"SET app.current_page_scope_user_id = '{escaped_psid}'"
                )

            yield conn
        except Exception as e:
            logger.error(f"Suggest response reader connection error: {e}")
            raise


# ================================================================
# SUGGEST RESPONSE WRITER CONNECTION POOL (conversation-scoped)
# ================================================================


async def get_suggest_response_writer_connection_pool() -> asyncpg.Pool:
    """
    Get or create the suggest response writer connection pool.
    This pool uses the suggest_response_writer role with conversation-scoped RLS.

    Returns:
        asyncpg.Pool: The suggest response writer connection pool instance
    """
    global _suggest_response_writer_pool

    if _suggest_response_writer_pool is None:
        async with _suggest_response_writer_pool_lock:
            if _suggest_response_writer_pool is None:
                try:
                    _suggest_response_writer_pool = await asyncpg.create_pool(
                        host=settings.postgres_host,
                        port=settings.postgres_port,
                        database=settings.postgres_db_name,
                        user=settings.postgres_suggest_response_writer_user,
                        password=settings.postgres_suggest_response_writer_password,
                        min_size=2,  # Smaller pool for write operations
                        max_size=8,  # Limited concurrent writes
                        command_timeout=15,  # Slightly longer for writes
                        server_settings={
                            "application_name": "elripley_suggest_response_writer",
                        },
                    )
                    logger.info(
                        "Suggest response writer connection pool created successfully"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to create suggest response writer connection pool: {e}"
                    )
                    raise

    return _suggest_response_writer_pool


async def close_suggest_response_writer_connection_pool():
    """Close the suggest response writer connection pool."""
    global _suggest_response_writer_pool

    if _suggest_response_writer_pool:
        async with _suggest_response_writer_pool_lock:
            if _suggest_response_writer_pool:
                await _suggest_response_writer_pool.close()
                _suggest_response_writer_pool = None
                logger.info("Suggest response writer connection pool closed")


@asynccontextmanager
async def get_suggest_response_writer_transaction(
    user_id: str,
    conversation_type: str,
    conversation_id: str,
    fan_page_id: str,
    page_scope_user_id: Optional[str] = None,
) -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Get a suggest response writer connection with transaction and conversation-scoped RLS.

    Sets multiple session variables for conversation-scoped RLS policies and wraps
    operations in a transaction for atomicity.

    Args:
        user_id: Owner user ID for RLS filtering
        conversation_type: 'messages' or 'comments'
        conversation_id: The conversation ID being served
        fan_page_id: The fan page ID
        page_scope_user_id: PSID (required for messages, optional for comments)

    Yields:
        asyncpg.Connection: Suggest response writer connection with transaction and RLS context
    """
    pool = await get_suggest_response_writer_connection_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # Set all RLS context variables using SET LOCAL (transaction-scoped)
                escaped_user_id = user_id.replace("'", "''")
                escaped_conv_type = conversation_type.replace("'", "''")
                escaped_conv_id = conversation_id.replace("'", "''")
                escaped_fan_page_id = fan_page_id.replace("'", "''")

                await conn.execute(
                    f"SET LOCAL app.current_user_id = '{escaped_user_id}'"
                )
                await conn.execute(
                    f"SET LOCAL app.current_conversation_type = '{escaped_conv_type}'"
                )
                await conn.execute(
                    f"SET LOCAL app.current_conversation_id = '{escaped_conv_id}'"
                )
                await conn.execute(
                    f"SET LOCAL app.current_fan_page_id = '{escaped_fan_page_id}'"
                )

                if page_scope_user_id:
                    escaped_psid = page_scope_user_id.replace("'", "''")
                    await conn.execute(
                        f"SET LOCAL app.current_page_scope_user_id = '{escaped_psid}'"
                    )

                yield conn
            except Exception as e:
                logger.error(f"Suggest response writer transaction error: {e}")
                raise


@asynccontextmanager
async def get_async_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Get an async database connection from the pool.

    Yields:
        asyncpg.Connection: Async database connection

    Example:
        async with get_async_connection() as conn:
            result = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
            return dict(result) if result else None
    """
    pool = await get_async_connection_pool()

    async with pool.acquire() as conn:
        try:
            yield conn
        except Exception as e:
            logger.error(f"Async database connection error: {e}")
            raise


@asynccontextmanager
async def async_db_transaction() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Async database transaction context manager.

    Automatically handles transaction lifecycle:
    - Begins transaction
    - Commits on success
    - Rolls back on exception
    - Ensures connection is returned to pool

    Yields:
        asyncpg.Connection: Database connection with active transaction

    Example:
        async with async_db_transaction() as conn:
            # Multiple operations in single transaction
            user_id = await create_user_async(conn, user_data)
            await create_facebook_user_async(conn, fb_data, user_id)
            # Auto commit on success, rollback on exception
    """
    async with get_async_connection() as conn:
        async with conn.transaction():
            try:
                yield conn
                logger.debug("Async transaction committed successfully")
            except Exception as e:
                # Rollback is automatic with asyncpg transaction context
                logger.error(f"Async transaction rolled back due to error: {e}")
                raise


@asynccontextmanager
async def async_db_savepoint(
    conn: asyncpg.Connection, savepoint_name: str = "sp1"
) -> AsyncGenerator[None, None]:
    """
    Create an async savepoint within an existing transaction.

    Args:
        conn: Active async database connection
        savepoint_name: Name for the savepoint

    Yields:
        None

    Example:
        async with async_db_transaction() as conn:
            await create_user_async(conn, user_data)

            try:
                async with async_db_savepoint(conn, "before_facebook"):
                    await create_facebook_user_async(conn, fb_data)
                    # This might fail
                    await risky_operation_async(conn)
            except Exception:
                # Facebook user creation is rolled back
                # but user creation is preserved
                logger.warning("Facebook user creation failed, continuing without it")
    """
    # Sanitize savepoint name to prevent SQL injection
    # Only allow alphanumeric and underscore characters
    import re

    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", savepoint_name)

    try:
        # Create savepoint using SQL command
        await conn.execute(f'SAVEPOINT "{safe_name}"')
        try:
            yield
            # Release savepoint on success
            await conn.execute(f'RELEASE SAVEPOINT "{safe_name}"')
        except Exception as e:
            # Rollback to savepoint on error
            await conn.execute(f'ROLLBACK TO SAVEPOINT "{safe_name}"')
            logger.warning(f"Rolled back to savepoint {safe_name}: {e}")
            raise
    except Exception as e:
        # If savepoint creation fails, log and re-raise
        logger.error(f"Failed to create savepoint {safe_name}: {e}")
        raise


async def check_async_database_connection() -> bool:
    """
    Check if async database connection is working.

    Returns:
        bool: True if connection successful, False otherwise
    """
    try:
        async with get_async_connection() as conn:
            await conn.fetchval("SELECT 1")
            return True
    except Exception as e:
        logger.error(f"Async database connection check failed: {e}")
        return False


# Performance monitoring utilities
class AsyncConnectionStats:
    """Track async connection pool statistics."""

    @staticmethod
    async def get_pool_stats() -> dict:
        """Get current connection pool statistics."""
        pool = await get_async_connection_pool()
        return {
            "size": pool.get_size(),
            "min_size": pool.get_min_size(),
            "max_size": pool.get_max_size(),
            "idle_connections": pool.get_idle_size(),
            "active_connections": pool.get_size() - pool.get_idle_size(),
        }

    @staticmethod
    async def log_pool_stats():
        """Log current pool statistics."""
        stats = await AsyncConnectionStats.get_pool_stats()
        logger.info(f"Async Pool Stats: {stats}")


# FastAPI startup/shutdown handlers
async def startup_async_database():
    """Initialize async database pools on FastAPI startup."""
    try:
        # Initialize main connection pool
        await get_async_connection_pool()
        logger.info("Async database initialized successfully")

        # Test main connection
        if await check_async_database_connection():
            logger.info("Async database connection test passed")
        else:
            logger.error("Async database connection test failed")

        # Initialize agent reader connection pool
        try:
            await get_agent_reader_connection_pool()
            logger.info("Agent reader database pool initialized successfully")
        except Exception as e:
            # Agent pool is optional - log warning but don't fail startup
            logger.warning(
                f"Agent reader pool not available (RLS may not be configured): {e}"
            )

        # Initialize agent writer connection pool
        try:
            await get_agent_writer_connection_pool()
            logger.info("Agent writer database pool initialized successfully")
        except Exception as e:
            # Agent pool is optional - log warning but don't fail startup
            logger.warning(
                f"Agent writer pool not available (RLS may not be configured): {e}"
            )

        # Initialize suggest response reader connection pool
        try:
            await get_suggest_response_reader_connection_pool()
            logger.info(
                "Suggest response reader database pool initialized successfully"
            )
        except Exception as e:
            # Pool is optional - log warning but don't fail startup
            logger.warning(
                f"Suggest response reader pool not available (RLS may not be configured): {e}"
            )

        # Initialize suggest response writer connection pool
        try:
            await get_suggest_response_writer_connection_pool()
            logger.info(
                "Suggest response writer database pool initialized successfully"
            )
        except Exception as e:
            # Pool is optional - log warning but don't fail startup
            logger.warning(
                f"Suggest response writer pool not available (RLS may not be configured): {e}"
            )

    except Exception as e:
        logger.error(f"Failed to initialize async database: {e}")
        raise


async def shutdown_async_database():
    """Close async database pools on FastAPI shutdown."""
    try:
        await close_async_connection_pool()
        await close_agent_reader_connection_pool()
        await close_agent_writer_connection_pool()
        await close_suggest_response_reader_connection_pool()
        await close_suggest_response_writer_connection_pool()
        logger.info("Async database shutdown completed")
    except Exception as e:
        logger.error(f"Error during async database shutdown: {e}")


# Migration helper: Dual mode support
class DatabaseMode:
    """Helper to support both sync and async modes during migration."""

    @staticmethod
    def is_async_available() -> bool:
        """Check if async pool is available."""
        return _async_connection_pool is not None

    @staticmethod
    def is_agent_reader_pool_available() -> bool:
        """Check if agent reader pool is available."""
        return _agent_reader_pool is not None

    @staticmethod
    def is_agent_writer_pool_available() -> bool:
        """Check if agent writer pool is available."""
        return _agent_writer_pool is not None

    @staticmethod
    def is_suggest_response_reader_pool_available() -> bool:
        """Check if suggest response reader pool is available."""
        return _suggest_response_reader_pool is not None

    @staticmethod
    def is_suggest_response_writer_pool_available() -> bool:
        """Check if suggest response writer pool is available."""
        return _suggest_response_writer_pool is not None

    @staticmethod
    async def get_connection_info() -> dict:
        """Get information about current connection setup."""
        info = {
            "async_pool_available": DatabaseMode.is_async_available(),
            "async_pool_stats": None,
            "agent_reader_pool_available": DatabaseMode.is_agent_reader_pool_available(),
            "agent_reader_pool_stats": None,
            "agent_writer_pool_available": DatabaseMode.is_agent_writer_pool_available(),
            "agent_writer_pool_stats": None,
            "suggest_response_reader_pool_available": DatabaseMode.is_suggest_response_reader_pool_available(),
            "suggest_response_reader_pool_stats": None,
            "suggest_response_writer_pool_available": DatabaseMode.is_suggest_response_writer_pool_available(),
            "suggest_response_writer_pool_stats": None,
        }

        if DatabaseMode.is_async_available():
            info["async_pool_stats"] = await AsyncConnectionStats.get_pool_stats()

        if DatabaseMode.is_agent_reader_pool_available():
            pool = _agent_reader_pool
            info["agent_reader_pool_stats"] = {
                "size": pool.get_size(),
                "min_size": pool.get_min_size(),
                "max_size": pool.get_max_size(),
                "idle_connections": pool.get_idle_size(),
                "active_connections": pool.get_size() - pool.get_idle_size(),
            }

        if DatabaseMode.is_agent_writer_pool_available():
            pool = _agent_writer_pool
            info["agent_writer_pool_stats"] = {
                "size": pool.get_size(),
                "min_size": pool.get_min_size(),
                "max_size": pool.get_max_size(),
                "idle_connections": pool.get_idle_size(),
                "active_connections": pool.get_size() - pool.get_idle_size(),
            }

        if DatabaseMode.is_suggest_response_reader_pool_available():
            pool = _suggest_response_reader_pool
            info["suggest_response_reader_pool_stats"] = {
                "size": pool.get_size(),
                "min_size": pool.get_min_size(),
                "max_size": pool.get_max_size(),
                "idle_connections": pool.get_idle_size(),
                "active_connections": pool.get_size() - pool.get_idle_size(),
            }

        if DatabaseMode.is_suggest_response_writer_pool_available():
            pool = _suggest_response_writer_pool
            info["suggest_response_writer_pool_stats"] = {
                "size": pool.get_size(),
                "min_size": pool.get_min_size(),
                "max_size": pool.get_max_size(),
                "idle_connections": pool.get_idle_size(),
                "active_connections": pool.get_size() - pool.get_idle_size(),
            }

        return info
