from typing import Any, Dict, List, Optional, Tuple

from src.redis_client.redis_agent_manager import RedisAgentManager
from src.utils.logger import get_logger


logger = get_logger()


class TempContextService:
    """Service wrapper around RedisAgentManager for temp agent context.

    This keeps all Redis operations in one place so that
    `AgentContextManager` can focus on orchestration.
    """

    def __init__(self, redis_manager: RedisAgentManager) -> None:
        self.redis = redis_manager

    async def create(
        self,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
        messages: List[Tuple[str, Dict[str, Any]]],
    ) -> bool:
        """Create temp context for main agent."""
        return await self.redis.set_temp_context(
            user_id, conversation_id, agent_response_id, messages
        )

    async def get(
        self,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
    ) -> Optional[list]:
        """Get temp context for current branch."""
        return await self.redis.get_temp_context(
            user_id, conversation_id, agent_response_id
        )

    async def create_for_subagent(
        self,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
        system_prompt: str,
        user_prompt: str,
    ) -> bool:
        """Create temp context for subagent with fixed system + user prompts."""
        try:
            from src.agent.utils import ensure_content_items
            from src.database.postgres.utils import generate_uuid

            temp_context_messages: List[Tuple[str, Dict[str, Any]]] = []

            system_message_dict: Dict[str, Any] = {
                "type": "message",
                "role": "system",
                "content": ensure_content_items(system_prompt, "system"),
            }
            temp_context_messages.append(("__system__", system_message_dict))

            user_message_id = generate_uuid()
            user_message_dict: Dict[str, Any] = {
                "type": "message",
                "role": "user",
                "content": ensure_content_items(user_prompt, "user"),
            }
            temp_context_messages.append((user_message_id, user_message_dict))

            return await self.redis.set_temp_context(
                user_id,
                conversation_id,
                agent_response_id,
                temp_context_messages,
            )
        except Exception as exc:
            logger.error("Error creating temp context for subagent: %s", exc)
            raise

    async def append_messages(
        self,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
        messages: List[Tuple[str, Dict[str, Any]]],
    ) -> None:
        """Append messages to existing temp context."""
        await self.redis.append_openai_messages_to_temp_context(
            user_id, conversation_id, agent_response_id, messages
        )

    async def update_system_message(
        self,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
        new_system_content: List[Dict[str, Any]],
    ) -> None:
        """Update system message content in temp context."""
        await self.redis.update_system_message_in_temp_context(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_resp_id=agent_response_id,
            new_system_content=new_system_content,
        )

    async def delete(
        self,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
    ) -> None:
        """Delete temp context when run is finalized."""
        await self.redis.delete_temp_context(
            user_id, conversation_id, agent_response_id
        )


__all__ = ["TempContextService"]
