"""Redis cache for suggest response content hashes and locks."""

import hashlib
import json
from typing import Any, Dict, Optional

from src.redis_client.redis_client import RedisClient
from src.utils.logger import get_logger

logger = get_logger()


class RedisSuggestResponseCache:
    """Cache for suggest response content hashes and generation locks."""

    def __init__(self, redis_client: RedisClient):
        self.redis_client = redis_client
        self.default_ttl = 3600  # 1 hour
        self.lock_ttl = 120  # 2 minutes for generation lock
        self.queue_ttl = 3600  # 1 hour for queued requests

    def _build_key(self, conversation_type: str, conversation_id: str) -> str:
        return f"suggest_response:content_hash:{conversation_type}:{conversation_id}"

    def _build_lock_key(
        self, user_id: str, conversation_type: str, conversation_id: str
    ) -> str:
        return f"suggest_response:lock:{user_id}:{conversation_type}:{conversation_id}"

    def _build_queue_key(
        self, user_id: str, conversation_type: str, conversation_id: str
    ) -> str:
        return f"suggest_response:queue:{user_id}:{conversation_type}:{conversation_id}"

    def _build_debounce_key(
        self, user_id: str, conversation_type: str, conversation_id: str
    ) -> str:
        return (
            f"suggest_response:debounce:{user_id}:{conversation_type}:{conversation_id}"
        )

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute SHA256 hash of content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def get_content_hash(
        self, conversation_type: str, conversation_id: str
    ) -> Optional[str]:
        """Get stored content hash for a conversation."""
        key = self._build_key(conversation_type, conversation_id)
        try:
            return await self.redis_client.get(key)
        except Exception as e:
            logger.warning(
                f"Redis suggest_response cache: failed to get hash for {key}: {e}"
            )
            return None

    async def set_content_hash(
        self,
        conversation_type: str,
        conversation_id: str,
        content_hash: str,
        ttl: Optional[int] = None,
    ) -> bool:
        """Store content hash for a conversation with optional TTL."""
        key = self._build_key(conversation_type, conversation_id)
        expire = ttl if ttl is not None else self.default_ttl
        try:
            return await self.redis_client.set(key, content_hash, expire=expire)
        except Exception as e:
            logger.warning(
                f"Redis suggest_response cache: failed to set hash for {key}: {e}"
            )
            return False

    async def should_skip_generation(
        self,
        conversation_type: str,
        conversation_id: str,
        current_hash: str,
    ) -> bool:
        """
        Return True if current_hash equals stored hash (no content change).
        Return False if no stored hash or hash differs (should generate).
        """
        stored = await self.get_content_hash(conversation_type, conversation_id)
        if stored is None:
            return False
        return stored == current_hash

    # ================================================================
    # LOCK OPERATIONS - prevent concurrent generation per user+conversation
    # ================================================================

    async def acquire_lock(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """
        Try to acquire generation lock for a user+conversation.
        Returns True if lock acquired, False if already locked.
        """
        key = self._build_lock_key(user_id, conversation_type, conversation_id)
        ttl = ttl_seconds if ttl_seconds is not None else self.lock_ttl

        # Use Lua script for atomic SET NX EX
        acquire_script = """
        local ttl = tonumber(ARGV[1]) or 120
        if ttl <= 0 then
            ttl = 120
        end
        if redis.call('set', KEYS[1], '1', 'EX', ttl, 'NX') then
            return 1
        end
        return 0
        """

        try:
            result = await self.redis_client.eval(acquire_script, 1, key, str(ttl))
            acquired = bool(result)
            if not acquired:
                logger.debug(
                    f"Suggest response lock already held for {user_id}:{conversation_type}:{conversation_id}"
                )
            return acquired
        except Exception as e:
            logger.warning(
                f"Redis suggest_response cache: failed to acquire lock for {key}: {e}"
            )
            # On error, allow generation to proceed (graceful degradation)
            return True

    async def release_lock(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
    ) -> bool:
        """Release generation lock for a user+conversation."""
        key = self._build_lock_key(user_id, conversation_type, conversation_id)
        try:
            await self.redis_client.delete(key)
            return True
        except Exception as e:
            logger.warning(
                f"Redis suggest_response cache: failed to release lock for {key}: {e}"
            )
            return False

    # ================================================================
    # QUEUE OPERATIONS - FIFO queue for webhook requests when locked
    # ================================================================

    async def enqueue_webhook_request(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        request_data: Dict[str, Any],
    ) -> bool:
        """Enqueue a webhook request when lock is held. Returns True on success."""
        key = self._build_queue_key(user_id, conversation_type, conversation_id)
        try:
            value = json.dumps(request_data)
            await self.redis_client.lpush(key, value)
            await self.redis_client.expire(key, self.queue_ttl)
            return True
        except Exception as e:
            logger.warning(
                f"Redis suggest_response cache: failed to enqueue for {key}: {e}"
            )
            return False

    async def dequeue_webhook_request(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Dequeue next webhook request (FIFO). Returns None if queue empty."""
        key = self._build_queue_key(user_id, conversation_type, conversation_id)
        try:
            value = await self.redis_client.rpop(key)
            if value is None:
                return None
            return json.loads(value)
        except Exception as e:
            logger.warning(
                f"Redis suggest_response cache: failed to dequeue for {key}: {e}"
            )
            return None

    async def get_queue_length(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
    ) -> int:
        """Get number of requests in queue."""
        key = self._build_queue_key(user_id, conversation_type, conversation_id)
        try:
            return await self.redis_client.llen(key)
        except Exception as e:
            logger.warning(
                f"Redis suggest_response cache: failed to get queue length for {key}: {e}"
            )
            return 0

    # ================================================================
    # DEBOUNCE OPERATIONS - per user+conversation for webhook triggers
    # ================================================================

    async def set_debounce_marker(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        marker_id: str,
        ttl_seconds: int,
    ) -> bool:
        """Set debounce marker with TTL. Returns True on success."""
        key = self._build_debounce_key(user_id, conversation_type, conversation_id)
        try:
            return await self.redis_client.set(key, marker_id, expire=ttl_seconds)
        except Exception as e:
            logger.warning(
                f"Redis suggest_response cache: failed to set debounce marker for {key}: {e}"
            )
            return False

    async def get_debounce_marker(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
    ) -> Optional[str]:
        """Get current debounce marker. Returns None if key missing or expired."""
        key = self._build_debounce_key(user_id, conversation_type, conversation_id)
        try:
            return await self.redis_client.get(key)
        except Exception as e:
            logger.warning(
                f"Redis suggest_response cache: failed to get debounce marker for {key}: {e}"
            )
            return None
