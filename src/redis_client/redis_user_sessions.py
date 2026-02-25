"""User session state helpers backed by Redis."""

import json
from typing import List, Optional

from src.utils.logger import get_logger
from .redis_client import RedisClient

logger = get_logger()


class RedisUserSessions:
    """Manages user session state in Redis."""

    def __init__(self, redis_client: RedisClient):
        """Initialize Redis user sessions."""
        self.redis = redis_client

    # ============================================================================
    # USER SESSION MANAGEMENT (Hash Structure)
    # ============================================================================

    async def add_user_session(self, user_id: str, socket_id: str) -> bool:
        try:
            key = f"user:{user_id}:session"
            field = f"sid:{socket_id}"
            session_data = {"is_online": True}
            session_json = json.dumps(session_data)

            # Add session to user's session hash
            # No TTL - data persists while user is online
            await self.redis.hset(key, field, session_json)

            # Add reverse lookup: session_id -> user_id
            # No TTL - data persists while user is online
            reverse_key = f"session:{socket_id}"
            await self.redis.set(reverse_key, user_id)

            return True

        except Exception as e:
            logger.error(f"Error adding session {socket_id} for user {user_id}: {e}")
            return False

    async def remove_user_session(self, user_id: str, socket_id: str) -> bool:
        """Remove a specific session for a user."""
        try:
            key = f"user:{user_id}:session"
            field = f"sid:{socket_id}"

            # Remove session from user's session hash
            result = await self.redis.hdel(key, field)

            # Remove reverse lookup
            reverse_key = f"session:{socket_id}"
            await self.redis.delete(reverse_key)

            if result > 0:
                return True
            else:
                logger.warning(f"Session {socket_id} not found for user {user_id}")
                return False

        except Exception as e:
            logger.error(f"Error removing session {socket_id} for user {user_id}: {e}")
            return False

    async def get_user_by_session_id(self, socket_id: str) -> Optional[str]:
        """Get user_id by session_id using reverse lookup."""
        try:
            reverse_key = f"session:{socket_id}"
            user_id = await self.redis.get(reverse_key)
            # Redis client is configured with decode_responses=True, so user_id is already a string
            return user_id if user_id else None

        except Exception as e:
            logger.error(f"Error getting user by session {socket_id}: {e}")
            return None

    # ============================================================================
    # UTILITY METHODS
    # ============================================================================

    async def get_all_user_keys(self, user_id: str) -> List[str]:
        """Get all keys for a user using SCAN (safe, non-blocking)."""
        try:
            keys = []
            cursor = 0
            pattern = f"user:{user_id}:*"

            # Use SCAN with small count to avoid blocking
            while True:
                cursor, batch_keys = await self.redis.scan(
                    cursor, match=pattern, count=50
                )
                keys.extend(batch_keys)
                if cursor == 0:
                    break

            return keys
        except Exception as e:
            logger.error(f"Error getting all keys for user {user_id}: {e}")
            return []

    async def remove_ttl_from_user_keys(self, user_id: str) -> bool:
        """
        Remove TTL from all user keys (persist forever).
        Called when user connects to ensure data stays while user is online.
        """
        try:
            keys = await self.get_all_user_keys(user_id)
            if not keys:
                return True

            # Check TTL for all keys first, then persist in batches
            batch_size = 50
            keys_to_persist = []

            # Check TTL in batches
            for i in range(0, len(keys), batch_size):
                batch = keys[i : i + batch_size]
                pipe = await self.redis.pipeline()

                for key in batch:
                    pipe.ttl(key)

                ttls = await pipe.execute()

                # Collect keys that have TTL > 0
                for key, ttl in zip(batch, ttls):
                    if ttl > 0:  # Only persist keys with TTL
                        keys_to_persist.append(key)

            # Persist keys in batches
            for i in range(0, len(keys_to_persist), batch_size):
                batch = keys_to_persist[i : i + batch_size]
                pipe = await self.redis.pipeline()

                for key in batch:
                    pipe.persist(key)

                await pipe.execute()

            return True

        except Exception as e:
            logger.error(f"Error removing TTL from keys for user {user_id}: {e}")
            return False

    async def set_ttl_for_user_keys(self, user_id: str, ttl_seconds: int = 900) -> bool:
        """
        Set TTL for all user keys (15 minutes default).
        Called when user disconnects and has no other active sessions.
        """
        try:
            keys = await self.get_all_user_keys(user_id)
            if not keys:
                return True

            # Process in batches using pipeline for efficiency
            batch_size = 50

            for i in range(0, len(keys), batch_size):
                batch = keys[i : i + batch_size]
                pipe = await self.redis.pipeline()

                for key in batch:
                    pipe.expire(key, ttl_seconds)

                await pipe.execute()

            return True

        except Exception as e:
            logger.error(f"Error setting TTL for keys for user {user_id}: {e}")
            return False

    async def is_user_online(self, user_id: str) -> bool:
        """Check if user has any active sessions."""
        try:
            key = f"user:{user_id}:session"
            # Key exists when user has at least one session
            return await self.redis.exists(key)
        except Exception as e:
            logger.error(f"Error checking online status for user {user_id}: {e}")
            return False

    async def has_other_active_sessions(
        self, user_id: str, exclude_socket_id: str
    ) -> bool:
        """
        Check if user has other active sessions besides the excluded one.
        Returns True if user has other sessions, False if this is the last session.
        """
        try:
            key = f"user:{user_id}:session"
            session_fields = await self.redis.hgetall(key)

            if not session_fields:
                return False

            # Count sessions excluding the current one
            for field in session_fields.keys():
                # Extract socket_id from field pattern: sid:{socket_id}
                socket_id = field.split(":", 1)[1] if ":" in field else field
                if socket_id != exclude_socket_id:
                    return True

            return False

        except Exception as e:
            logger.error(f"Error checking other sessions for user {user_id}: {e}")
            # On error, assume no other sessions to be safe (won't set TTL prematurely)
            return False
