"""
Redis locks for Facebook sync operations.

Provides generic locking mechanism to prevent concurrent sync operations.
Uses separate key namespace from agent locks to avoid conflicts.

Redis keys:
- sync:full:{page_id} - Full sync lock (posts + comments)
- sync:posts:{page_id} - Posts sync lock
- sync:comments:{post_id} - Comments sync lock
- sync:inbox:{page_id} - Inbox sync lock
"""

from typing import Optional
from src.redis_client.redis_client import RedisClient
from src.utils.logger import get_logger

logger = get_logger()


class RedisFacebookSyncLocks:
    """Provides generic locking utilities for all Facebook sync operations."""

    def __init__(self, redis_client: Optional[RedisClient] = None):
        """Initialize sync locks with Redis client."""
        self.redis_client = redis_client

    async def acquire_lock(self, lock_key: str, ttl_seconds: int = 3600) -> bool:
        """
        Acquire a generic sync lock.

        Args:
            lock_key: Full Redis key (e.g., "sync:posts:123", "sync:inbox:456")
            ttl_seconds: Lock TTL in seconds (default: 1 hour)

        Returns:
            True if lock acquired, False if already locked
        """
        return await self._acquire_lock(lock_key, ttl_seconds)

    async def release_lock(self, lock_key: str) -> bool:
        """
        Release a sync lock.

        Args:
            lock_key: Full Redis key

        Returns:
            True if lock released, False on error
        """
        return await self._release_lock(lock_key)

    async def _acquire_lock(self, lock_key: str, ttl_seconds: int) -> bool:
        """
        Internal helper to acquire a lock.

        Args:
            lock_key: Redis key for the lock
            ttl_seconds: Lock TTL in seconds

        Returns:
            True if lock acquired, False if already locked
        """
        if not self.redis_client:
            # If Redis not available, skip lock (graceful degradation)
            logger.warning(
                f"Redis client not available for {lock_key}, skipping lock check"
            )
            return True

        ttl = max(int(ttl_seconds or 3600), 1)

        # Use Lua script for atomic SET NX EX
        ACQUIRE_LOCK_SCRIPT = """
        local ttl = tonumber(ARGV[1]) or 3600
        if ttl <= 0 then
            ttl = 3600
        end

        if redis.call('set', KEYS[1], '1', 'EX', ttl, 'NX') then
            return 1
        end

        return 0
        """

        try:
            result = await self.redis_client.eval(
                ACQUIRE_LOCK_SCRIPT,
                1,
                lock_key,
                str(ttl),
            )
            acquired = bool(result)
            if acquired:
                logger.info(f"🔒 Lock acquired: {lock_key} (TTL: {ttl}s)")
            else:
                logger.warning(f"⚠️ Lock already held: {lock_key}")
            return acquired
        except Exception as e:
            logger.error(f"Error acquiring sync lock {lock_key}: {e}")
            # On error, allow sync to proceed (graceful degradation)
            return True

    async def _release_lock(self, lock_key: str) -> bool:
        """
        Internal helper to release a lock.

        Args:
            lock_key: Redis key for the lock

        Returns:
            True if lock released, False on error
        """
        if not self.redis_client:
            return True

        RELEASE_LOCK_SCRIPT = """
        redis.call('del', KEYS[1])
        return 1
        """

        try:
            result = await self.redis_client.eval(
                RELEASE_LOCK_SCRIPT,
                1,
                lock_key,
            )
            logger.info(f"🔓 Lock released: {lock_key}")
            return bool(result)
        except Exception as e:
            logger.error(f"Error releasing sync lock {lock_key}: {e}")
            return False
