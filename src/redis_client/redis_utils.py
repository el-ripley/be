"""Redis utility functions and helpers."""

from typing import Any, Dict, List, Optional
from datetime import datetime

from src.redis_client.redis_client import RedisClient

from src.utils.logger import get_logger

logger = get_logger()


class RedisUtils:
    """Utility functions for Redis operations."""

    def __init__(self, redis_client: RedisClient):
        """Initialize Redis utils."""
        self.redis = redis_client

    # ============================================================================
    # BULK OPERATIONS
    # ============================================================================

    async def bulk_set_json(
        self, data: Dict[str, Any], prefix: str = "", expire: Optional[int] = None
    ) -> bool:
        """Set multiple JSON values in one operation."""
        try:
            pipe = self.redis.pipeline()

            for key, value in data.items():
                full_key = f"{prefix}:{key}" if prefix else key
                pipe.set_json(full_key, value, expire=expire)

            await pipe.execute()
            return True
        except Exception as e:
            logger.error(f"Error in bulk_set_json: {e}")
            return False

    async def bulk_get_json(self, keys: List[str], prefix: str = "") -> Dict[str, Any]:
        """Get multiple JSON values in one operation."""
        try:
            pipe = self.redis.pipeline()

            for key in keys:
                full_key = f"{prefix}:{key}" if prefix else key
                pipe.get_json(full_key)

            results = await pipe.execute()

            # Combine keys with results
            return {
                key: result for key, result in zip(keys, results) if result is not None
            }
        except Exception as e:
            logger.error(f"Error in bulk_get_json: {e}")
            return {}

    # ============================================================================
    # PATTERN OPERATIONS
    # ============================================================================

    async def get_keys_by_pattern(self, pattern: str) -> List[str]:
        """Get all keys matching a pattern using SCAN for better performance."""
        try:
            keys = []
            cursor = 0

            while True:
                cursor, batch_keys = await self.redis.scan(
                    cursor, match=pattern, count=100
                )
                keys.extend(batch_keys)
                if cursor == 0:
                    break

            return keys
        except Exception as e:
            logger.error(f"Error getting keys by pattern {pattern}: {e}")
            return []

    async def delete_keys_by_pattern(self, pattern: str) -> int:
        """Delete all keys matching a pattern."""
        try:
            keys = await self.get_keys_by_pattern(pattern)
            if keys:
                return await self.redis.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"Error deleting keys by pattern {pattern}: {e}")
            return 0

    # ============================================================================
    # USER-SPECIFIC UTILITIES
    # ============================================================================

    async def get_user_keys(self, user_id: str) -> List[str]:
        """Get all keys for a specific user."""
        pattern = f"user:{user_id}:*"
        return await self.get_keys_by_pattern(pattern)

    async def get_user_data_size(self, user_id: str) -> Dict[str, int]:
        """Get data size statistics for a user."""
        try:
            keys = await self.get_user_keys(user_id)

            total_size = 0
            key_count = len(keys)

            for key in keys:
                try:
                    # Get key type and size
                    key_type = await self.redis.type(key)
                    if key_type == "string":
                        value = await self.redis.get(key)
                        if value:
                            total_size += len(value.encode("utf-8"))
                    elif key_type == "hash":
                        hash_data = await self.redis.hgetall(key)
                        for field, val in hash_data.items():
                            total_size += len(field.encode("utf-8")) + len(
                                val.encode("utf-8")
                            )
                except Exception:
                    continue

            return {
                "key_count": key_count,
                "total_size_bytes": total_size,
                "total_size_kb": round(total_size / 1024, 2),
            }
        except Exception as e:
            logger.error(f"Error getting user data size for {user_id}: {e}")
            return {"key_count": 0, "total_size_bytes": 0, "total_size_kb": 0}

    # ============================================================================
    # CACHE MANAGEMENT
    # ============================================================================

    async def warm_up_cache(self, user_id: str, data: Dict[str, Any]) -> bool:
        """Warm up cache with user data."""
        try:
            # This can be used to preload user data
            for key, value in data.items():
                await self.redis.set_json(f"user:{user_id}:{key}", value, expire=3600)

            logger.info(f"Cache warmed up for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error warming up cache for user {user_id}: {e}")
            return False

    async def invalidate_user_cache(
        self, user_id: str, patterns: List[str] = None
    ) -> bool:
        """Invalidate user cache by patterns."""
        try:
            if patterns is None:
                patterns = ["*"]  # Invalidate all user data

            deleted_count = 0
            for pattern in patterns:
                full_pattern = f"user:{user_id}:{pattern}"
                count = await self.delete_keys_by_pattern(full_pattern)
                deleted_count += count

            logger.info(f"Invalidated {deleted_count} cache entries for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error invalidating cache for user {user_id}: {e}")
            return False

    # ============================================================================
    # HEALTH CHECK
    # ============================================================================

    async def health_check(self) -> Dict[str, Any]:
        """Perform Redis health check."""
        try:
            # Test basic operations
            test_key = "health_check:test"
            test_value = {"timestamp": datetime.now().isoformat(), "test": True}

            # Test set/get
            await self.redis.set_json(test_key, test_value, expire=10)
            retrieved = await self.redis.get_json(test_key)

            # Test connection
            is_connected = await self.redis.is_connected()

            # Clean up
            await self.redis.delete(test_key)

            return {
                "status": (
                    "healthy"
                    if is_connected and retrieved == test_value
                    else "unhealthy"
                ),
                "connected": is_connected,
                "read_write_test": retrieved == test_value,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return {
                "status": "unhealthy",
                "connected": False,
                "read_write_test": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    # ============================================================================
    # STATISTICS
    # ============================================================================

    async def get_redis_stats(self) -> Dict[str, Any]:
        """Get Redis statistics."""
        try:
            info = await self.redis.info()

            return {
                "redis_version": info.get("redis_version", "unknown"),
                "used_memory": info.get("used_memory_human", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
                "total_commands_processed": info.get("total_commands_processed", 0),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
                "uptime_in_seconds": info.get("uptime_in_seconds", 0),
            }
        except Exception as e:
            logger.error(f"Error getting Redis stats: {e}")
            return {"error": str(e)}
