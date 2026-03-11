"""Redis client for caching and session management."""

import json
from datetime import timedelta
from typing import Any, Dict, List, Optional, Union

import redis.asyncio as aioredis
from redis.asyncio import Redis

from src.settings import settings
from src.utils.logger import get_logger

logger = get_logger()


class RedisClient:
    """Redis client wrapper with connection management and utility methods."""

    def __init__(self):
        """Initialize Redis client."""
        self._redis: Optional[Redis] = None
        self._connection_url = settings.redis_connection_url

    async def connect(self) -> None:
        """Establish connection to Redis."""
        try:
            self._redis = aioredis.from_url(
                self._connection_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=300,  # 5 minutes - needed for blocking operations like brpop
                retry_on_timeout=True,
            )
            # Test connection
            await self._redis.ping()
            logger.info("Successfully connected to Redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None
            logger.info("Disconnected from Redis")

    async def is_connected(self) -> bool:
        """Check if Redis is connected."""
        if not self._redis:
            return False
        try:
            await self._redis.ping()
            return True
        except Exception:
            return False

    async def get(self, key: str) -> Optional[str]:
        """Get value by key."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.get(key)
        except Exception as e:
            logger.error(f"Error getting key {key}: {e}")
            return None

    async def set(
        self,
        key: str,
        value: Union[str, dict, list],
        expire: Optional[Union[int, timedelta]] = None,
    ) -> bool:
        """Set key-value pair with optional expiration."""
        if not await self.is_connected():
            await self.connect()

        try:
            # Serialize non-string values to JSON
            if isinstance(value, (dict, list)):
                value = json.dumps(value)

            await self._redis.set(key, value, ex=expire)
            return True
        except Exception as e:
            logger.error(f"Error setting key {key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Delete key."""
        if not await self.is_connected():
            await self.connect()

        try:
            result = await self._redis.delete(key)
            return bool(result)
        except Exception as e:
            logger.error(f"Error deleting key {key}: {e}")
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        if not await self.is_connected():
            await self.connect()

        try:
            result = await self._redis.exists(key)
            return bool(result)
        except Exception as e:
            logger.error(f"Error checking existence of key {key}: {e}")
            return False

    async def get_json(self, key: str) -> Optional[Any]:
        """Get and deserialize JSON value."""
        value = await self.get(key)
        if value is None:
            return None

        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            logger.error(f"Error deserializing JSON for key {key}: {e}")
            return None

    async def set_json(
        self, key: str, value: Any, expire: Optional[Union[int, timedelta]] = None
    ) -> bool:
        """Set JSON-serialized value."""
        try:
            json_value = json.dumps(value)
            return await self.set(key, json_value, expire)
        except json.JSONEncodeError as e:
            logger.error(f"Error serializing JSON for key {key}: {e}")
            return False

    async def expire(self, key: str, seconds: int) -> bool:
        """Set expiration for key."""
        if not await self.is_connected():
            await self.connect()

        try:
            result = await self._redis.expire(key, seconds)
            return bool(result)
        except Exception as e:
            logger.error(f"Error setting expiration for key {key}: {e}")
            return False

    async def ttl(self, key: str) -> int:
        """Get time to live for key."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.ttl(key)
        except Exception as e:
            logger.error(f"Error getting TTL for key {key}: {e}")
            return -1

    async def persist(self, key: str) -> bool:
        """Remove expiration from key (persist forever)."""
        if not await self.is_connected():
            await self.connect()

        try:
            result = await self._redis.persist(key)
            return bool(result)
        except Exception as e:
            logger.error(f"Error persisting key {key}: {e}")
            return False

    # ============================================================================
    # HASH OPERATIONS
    # ============================================================================

    async def hset(
        self, key: str, field: str = None, value: str = None, mapping: dict = None
    ) -> int:
        """Set hash field(s)."""
        if not await self.is_connected():
            await self.connect()

        try:
            if mapping:
                return await self._redis.hset(key, mapping=mapping)
            else:
                return await self._redis.hset(key, field, value)
        except Exception as e:
            logger.error(f"Error setting hash field {field} for key {key}: {e}")
            return 0

    async def hget(self, key: str, field: str) -> Optional[str]:
        """Get hash field value."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.hget(key, field)
        except Exception as e:
            logger.error(f"Error getting hash field {field} for key {key}: {e}")
            return None

    async def hgetall(self, key: str) -> Dict[str, str]:
        """Get all hash fields and values."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.hgetall(key)
        except Exception as e:
            logger.error(f"Error getting all hash fields for key {key}: {e}")
            return {}

    async def hdel(self, key: str, *fields: str) -> int:
        """Delete hash field(s)."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.hdel(key, *fields)
        except Exception as e:
            logger.error(f"Error deleting hash fields {fields} for key {key}: {e}")
            return 0

    async def hexists(self, key: str, field: str) -> bool:
        """Check if hash field exists."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.hexists(key, field)
        except Exception as e:
            logger.error(f"Error checking hash field {field} for key {key}: {e}")
            return False

    async def hkeys(self, key: str) -> List[str]:
        """Get all hash field names."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.hkeys(key)
        except Exception as e:
            logger.error(f"Error getting hash keys for key {key}: {e}")
            return []

    async def hvals(self, key: str) -> List[str]:
        """Get all hash field values."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.hvals(key)
        except Exception as e:
            logger.error(f"Error getting hash values for key {key}: {e}")
            return []

    async def hlen(self, key: str) -> int:
        """Get number of hash fields."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.hlen(key)
        except Exception as e:
            logger.error(f"Error getting hash length for key {key}: {e}")
            return 0

    # ============================================================================
    # LIST OPERATIONS
    # ============================================================================

    async def lpush(self, key: str, *values: str) -> int:
        """Push value(s) to the left of list."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.lpush(key, *values)
        except Exception as e:
            logger.error(f"Error pushing to list {key}: {e}")
            return 0

    async def rpush(self, key: str, *values: str) -> int:
        """Push value(s) to the right of list."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.rpush(key, *values)
        except Exception as e:
            logger.error(f"Error pushing to list {key}: {e}")
            return 0

    async def lpop(self, key: str) -> Optional[str]:
        """Pop value from the left of list."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.lpop(key)
        except Exception as e:
            logger.error(f"Error popping from list {key}: {e}")
            return None

    async def rpop(self, key: str) -> Optional[str]:
        """Pop value from the right of list."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.rpop(key)
        except Exception as e:
            logger.error(f"Error popping from list {key}: {e}")
            return None

    async def brpop(
        self, keys: Union[str, List[str]], timeout: int = 0
    ) -> Optional[tuple]:
        """
        Blocking pop from the right of list(s).

        Args:
            keys: Single key string or list of keys
            timeout: Timeout in seconds (0 = wait indefinitely)

        Returns:
            Tuple (key, value) or None if timeout
        """
        if not await self.is_connected():
            await self.connect()

        try:
            if isinstance(keys, str):
                keys = [keys]
            result = await self._redis.brpop(keys, timeout=timeout)
            return result
        except Exception as e:
            # Connection error - close old connection and return None
            # Worker will retry on next loop iteration
            logger.warning(f"Redis connection error during brpop: {e}")
            try:
                # Close old connection if exists
                if self._redis:
                    try:
                        await self._redis.close()
                    except Exception:
                        pass
                    self._redis = None
            except Exception:
                pass
            return None

    async def blpop(
        self, keys: Union[str, List[str]], timeout: int = 0
    ) -> Optional[tuple]:
        """
        Blocking pop from the left of list(s).

        Args:
            keys: Single key string or list of keys
            timeout: Timeout in seconds (0 = wait indefinitely)

        Returns:
            Tuple (key, value) or None if timeout
        """
        if not await self.is_connected():
            await self.connect()

        try:
            if isinstance(keys, str):
                keys = [keys]
            result = await self._redis.blpop(keys, timeout=timeout)
            return result
        except Exception as e:
            logger.error(f"Error blocking pop from list {keys}: {e}")
            return None

    async def llen(self, key: str) -> int:
        """Get length of list."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.llen(key)
        except Exception as e:
            logger.error(f"Error getting list length for {key}: {e}")
            return 0

    async def lrange(self, key: str, start: int, end: int) -> List[str]:
        """Get range of list elements."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.lrange(key, start, end)
        except Exception as e:
            logger.error(f"Error getting list range for {key}: {e}")
            return []

    async def lindex(self, key: str, index: int) -> Optional[str]:
        """Get element at index in list."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.lindex(key, index)
        except Exception as e:
            logger.error(f"Error getting list index for {key}: {e}")
            return None

    # ============================================================================
    # KEY OPERATIONS
    # ============================================================================

    async def keys(self, pattern: str) -> List[str]:
        """Get all keys matching pattern."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.keys(pattern)
        except Exception as e:
            logger.error(f"Error getting keys with pattern {pattern}: {e}")
            return []

    # ============================================================================
    # SET OPERATIONS
    # ============================================================================

    async def sadd(self, key: str, *values: str) -> int:
        """Add member(s) to set."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.sadd(key, *values)
        except Exception as e:
            logger.error(f"Error adding to set {key}: {e}")
            return 0

    async def srem(self, key: str, *values: str) -> int:
        """Remove member(s) from set."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.srem(key, *values)
        except Exception as e:
            logger.error(f"Error removing from set {key}: {e}")
            return 0

    async def smembers(self, key: str) -> set:
        """Get all members of set."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.smembers(key)
        except Exception as e:
            logger.error(f"Error getting set members for {key}: {e}")
            return set()

    async def sismember(self, key: str, value: str) -> bool:
        """Check if value is member of set."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.sismember(key, value)
        except Exception as e:
            logger.error(f"Error checking set membership for {key}: {e}")
            return False

    async def scard(self, key: str) -> int:
        """Get number of members in set."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.scard(key)
        except Exception as e:
            logger.error(f"Error getting set cardinality for {key}: {e}")
            return 0

    # ============================================================================
    # SORTED SET (ZSET) OPERATIONS
    # ============================================================================

    async def zadd(self, key: str, mapping: dict) -> int:
        """Add member(s) to sorted set with scores."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.zadd(key, mapping)
        except Exception as e:
            logger.error(f"Error adding to sorted set {key}: {e}")
            return 0

    async def zrem(self, key: str, *members: str) -> int:
        """Remove member(s) from sorted set."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.zrem(key, *members)
        except Exception as e:
            logger.error(f"Error removing from sorted set {key}: {e}")
            return 0

    async def zrange(
        self, key: str, start: int, end: int, withscores: bool = False
    ) -> list:
        """Get members from sorted set by rank range."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.zrange(key, start, end, withscores=withscores)
        except Exception as e:
            logger.error(f"Error getting sorted set range for {key}: {e}")
            return []

    async def zrevrange(
        self, key: str, start: int, end: int, withscores: bool = False
    ) -> list:
        """Get members from sorted set by rank range (reverse order)."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.zrevrange(key, start, end, withscores=withscores)
        except Exception as e:
            logger.error(f"Error getting reverse sorted set range for {key}: {e}")
            return []

    async def zcard(self, key: str) -> int:
        """Get number of members in sorted set."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.zcard(key)
        except Exception as e:
            logger.error(f"Error getting sorted set cardinality for {key}: {e}")
            return 0

    async def zscore(self, key: str, member: str) -> Optional[float]:
        """Get score of member in sorted set."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.zscore(key, member)
        except Exception as e:
            logger.error(f"Error getting sorted set score for {key}: {e}")
            return None

    async def zremrangebyrank(self, key: str, start: int, end: int) -> int:
        """Remove members by rank range."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.zremrangebyrank(key, start, end)
        except Exception as e:
            logger.error(f"Error removing sorted set range for {key}: {e}")
            return 0

    # ============================================================================
    # SCAN OPERATIONS
    # ============================================================================

    async def scan(
        self, cursor: int = 0, match: str = None, count: int = None
    ) -> tuple:
        """Scan keys with pattern matching."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.scan(cursor, match=match, count=count)
        except Exception as e:
            logger.error(f"Error scanning keys with pattern {match}: {e}")
            return (0, [])

    # ============================================================================
    # BATCH OPERATIONS
    # ============================================================================

    async def pipeline(self):
        """Get Redis pipeline for batch operations."""
        if not await self.is_connected():
            await self.connect()

        return self._redis.pipeline()

    # ============================================================================
    # INFO OPERATIONS
    # ============================================================================

    async def info(self, section: str = None) -> Dict[str, Any]:
        """Get Redis server information."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.info(section)
        except Exception as e:
            logger.error(f"Error getting Redis info: {e}")
            return {}

    async def type(self, key: str) -> str:
        """Get key type."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.type(key)
        except Exception as e:
            logger.error(f"Error getting key type for {key}: {e}")
            return "none"

    # ============================================================================
    # LUA SCRIPT OPERATIONS
    # ============================================================================

    async def eval(self, script: str, num_keys: int, *keys_and_args) -> Any:
        """Execute Lua script atomically."""
        if not await self.is_connected():
            await self.connect()

        try:
            return await self._redis.eval(script, num_keys, *keys_and_args)
        except Exception as e:
            logger.error(f"Error executing Lua script: {e}")
            raise
