"""Aggregates Redis state handling (temp context, locks)."""

from src.utils.logger import get_logger
from .redis_client import RedisClient
from .redis_agent_temp_context import RedisAgentTempContextMixin
from .redis_agent_lock import RedisAgentLockMixin

logger = get_logger()


class RedisAgentManager(RedisAgentTempContextMixin, RedisAgentLockMixin):
    """Manages user state in Redis with dedicated concerns."""

    def __init__(self, redis_client: RedisClient):
        self.redis = redis_client
