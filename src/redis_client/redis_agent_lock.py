"""
Redis keys (locks):
- user:{user_id}:conversation_lock:{conversation_id}
  Type: String value "1" with EX TTL (default 180s), used with SET NX/EX
- stop:{user_id}:{conversation_id}:{agent_response_id}
  Type: String value "1" with EX TTL (default 300s), used for agent stop signal
"""

from src.utils.logger import get_logger

logger = get_logger()


class RedisAgentLockMixin:
    """Provides conversation-level locking utilities."""

    @staticmethod
    def _conversation_lock_key(user_id: str, conversation_id: str) -> str:
        return f"user:{user_id}:conversation_lock:{conversation_id}"

    async def acquire_conversation_lock(
        self,
        user_id: str,
        conversation_id: str,
        ttl_seconds: int = 180,
    ) -> bool:
        if not user_id or not conversation_id:
            return False

        ttl = max(int(ttl_seconds or 180), 1)
        key = self._conversation_lock_key(user_id, conversation_id)

        ACQUIRE_CONVERSATION_LOCK_SCRIPT = """
        local ttl = tonumber(ARGV[1]) or 60
        if ttl <= 0 then
            ttl = 60
        end

        if redis.call('set', KEYS[1], '1', 'EX', ttl, 'NX') then
            return 1
        end

        return 0
        """

        try:
            result = await self.redis.eval(
                ACQUIRE_CONVERSATION_LOCK_SCRIPT,
                1,
                key,
                str(ttl),
            )
            return bool(result)
        except Exception as e:
            logger.error(
                f"Error acquiring conversation lock for user {user_id}, "
                f"conversation {conversation_id}: {e}"
            )
            return False

    async def release_conversation_lock(
        self, user_id: str, conversation_id: str
    ) -> bool:
        if not user_id or not conversation_id:
            return False

        key = self._conversation_lock_key(user_id, conversation_id)

        RELEASE_CONVERSATION_LOCK_SCRIPT = """
        redis.call('del', KEYS[1])
        return 1
        """
        try:
            result = await self.redis.eval(
                RELEASE_CONVERSATION_LOCK_SCRIPT,
                1,
                key,
            )
            return bool(result)
        except Exception as e:
            logger.error(
                f"Error releasing conversation lock for user {user_id}, "
                f"conversation {conversation_id}: {e}"
            )
            return False

    @staticmethod
    def _agent_stop_signal_key(
        user_id: str, conversation_id: str, agent_response_id: str
    ) -> str:
        return f"stop:{user_id}:{conversation_id}:{agent_response_id}"

    async def set_agent_stop_signal(
        self,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
        ttl_seconds: int = 300,
    ) -> bool:
        """Set stop signal for agent execution."""
        if not user_id or not conversation_id or not agent_response_id:
            return False

        ttl = max(int(ttl_seconds or 300), 1)
        key = self._agent_stop_signal_key(user_id, conversation_id, agent_response_id)

        SET_STOP_SIGNAL_SCRIPT = """
        local ttl = tonumber(ARGV[1]) or 300
        if ttl <= 0 then
            ttl = 300
        end
        redis.call('set', KEYS[1], '1', 'EX', ttl)
        return 1
        """

        try:
            result = await self.redis.eval(
                SET_STOP_SIGNAL_SCRIPT,
                1,
                key,
                str(ttl),
            )
            return bool(result)
        except Exception as e:
            logger.error(
                f"Error setting stop signal for user {user_id}, "
                f"conversation {conversation_id}, agent_response {agent_response_id}: {e}"
            )
            return False

    async def check_agent_stop_signal(
        self,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
    ) -> bool:
        """Check if stop signal exists for agent execution."""
        if not user_id or not conversation_id or not agent_response_id:
            return False

        key = self._agent_stop_signal_key(user_id, conversation_id, agent_response_id)

        CHECK_STOP_SIGNAL_SCRIPT = """
        if redis.call('exists', KEYS[1]) == 1 then
            return 1
        end
        return 0
        """

        try:
            result = await self.redis.eval(
                CHECK_STOP_SIGNAL_SCRIPT,
                1,
                key,
            )
            return bool(result)
        except Exception as e:
            logger.error(
                f"Error checking stop signal for user {user_id}, "
                f"conversation {conversation_id}, agent_response {agent_response_id}: {e}"
            )
            return False

    async def clear_agent_stop_signal(
        self,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
    ) -> bool:
        """Clear stop signal for agent execution."""
        if not user_id or not conversation_id or not agent_response_id:
            return False

        key = self._agent_stop_signal_key(user_id, conversation_id, agent_response_id)

        CLEAR_STOP_SIGNAL_SCRIPT = """
        redis.call('del', KEYS[1])
        return 1
        """

        try:
            result = await self.redis.eval(
                CLEAR_STOP_SIGNAL_SCRIPT,
                1,
                key,
            )
            return bool(result)
        except Exception as e:
            logger.error(
                f"Error clearing stop signal for user {user_id}, "
                f"conversation {conversation_id}, agent_response {agent_response_id}: {e}"
            )
            return False
