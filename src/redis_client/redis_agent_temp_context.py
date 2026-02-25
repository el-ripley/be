"""
Redis keys (temp context):
- user:{user_id}:conv:{conversation_id}:current_branch:agent_resp:{agent_resp_id}
  Type: Hash. Field = message id (or fallback), value = JSON {"order": int, "message": dict}
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger()


class RedisAgentTempContextMixin:
    """Stores temporary agent response context in Redis using hash entries."""

    @staticmethod
    def _temp_context_key(
        user_id: str, conversation_id: str, agent_resp_id: str
    ) -> str:
        return f"user:{user_id}:conv:{conversation_id}:current_branch:agent_resp:{agent_resp_id}"

    async def set_temp_context(
        self,
        user_id: str,
        conversation_id: str,
        agent_resp_id: str,
        messages: List[Tuple[str, Dict[str, Any]]],
    ) -> bool:
        try:
            key = self._temp_context_key(user_id, conversation_id, agent_resp_id)

            # Clear any previous hash to avoid stale entries or misordered data.
            await self.redis.delete(key)

            mapping: Dict[str, str] = {}
            for idx, message in enumerate(messages):
                msg_id, sanitized_message = message
                payload = {"order": idx, "message": sanitized_message}
                mapping[msg_id] = json.dumps(payload)

            if mapping:
                await self.redis.hset(key, mapping=mapping)
            return True
        except Exception as e:
            logger.error(
                f"Error setting temp context for agent_resp {agent_resp_id} in conv {conversation_id} for user {user_id}: {e}"
            )
            return False

    async def get_temp_context(
        self,
        user_id: str,
        conversation_id: str,
        agent_resp_id: str,
    ) -> Optional[List[Dict[str, Any]]]:
        try:
            key = self._temp_context_key(user_id, conversation_id, agent_resp_id)
            raw_hash = await self.redis.hgetall(key)
            if not raw_hash:
                return None

            ordered_messages: List[Tuple[int, Dict[str, Any]]] = []
            for value in raw_hash.values():
                try:
                    payload = json.loads(value)
                    ordered_messages.append(
                        (int(payload.get("order", 0)), payload.get("message", {}))
                    )
                except Exception as exc:
                    logger.warning("Malformed temp context payload: %s", exc)
                    continue

            ordered_messages.sort(key=lambda item: item[0])
            return [msg for _, msg in ordered_messages]

        except Exception as e:
            logger.error(
                f"Error getting temp context for agent_resp {agent_resp_id} in conv {conversation_id} for user {user_id}: {e}"
            )
            return None

    async def append_openai_messages_to_temp_context(
        self,
        user_id: str,
        conversation_id: str,
        agent_resp_id: str,
        new_messages: List[Tuple[str, Dict[str, Any]]],
    ) -> bool:
        try:
            key = self._temp_context_key(user_id, conversation_id, agent_resp_id)
            existing_payload = await self.redis.hgetall(key)

            if not existing_payload:
                logger.warning(
                    f"No temp context found for agent_resp {agent_resp_id} in conv {conversation_id} for user {user_id}. Cannot append messages."
                )
                return False

            max_order = -1
            for value in existing_payload.values():
                try:
                    payload = json.loads(value)
                    max_order = max(max_order, int(payload.get("order", -1)))
                except Exception:
                    continue

            mapping: Dict[str, str] = {}
            for offset, message in enumerate(new_messages, start=max_order + 1):
                msg_id, sanitized_message = message
                payload = {"order": offset, "message": sanitized_message}
                mapping[msg_id] = json.dumps(payload)

            if mapping:
                await self.redis.hset(key, mapping=mapping)
            return True

        except Exception as e:
            logger.error(
                f"Error appending messages to temp context for agent_resp {agent_resp_id} in conv {conversation_id} for user {user_id}: {e}"
            )
            return False

    async def delete_temp_context(
        self,
        user_id: str,
        conversation_id: str,
        agent_resp_id: str,
    ) -> bool:
        try:
            key = self._temp_context_key(user_id, conversation_id, agent_resp_id)
            result = await self.redis.delete(key)

            if result > 0:
                return True
            else:
                logger.warning(
                    f"No temp context found to delete for agent_resp {agent_resp_id} in conv {conversation_id} for user {user_id}"
                )
                return False

        except Exception as e:
            logger.error(
                f"Error deleting temp context for agent_resp {agent_resp_id} in conv {conversation_id} for user {user_id}: {e}"
            )
            return False

    async def delete_messages_from_temp_context(
        self,
        user_id: str,
        conversation_id: str,
        agent_resp_id: str,
        message_ids: List[str],
    ) -> bool:
        """Remove specific message ids from the temp context hash."""
        if not message_ids:
            return True

        try:
            key = self._temp_context_key(user_id, conversation_id, agent_resp_id)
            await self.redis.hdel(key, *message_ids)
            return True
        except Exception as exc:
            logger.error(
                "Error deleting messages %s from temp context for agent_resp %s in conv %s for user %s: %s",
                message_ids,
                agent_resp_id,
                conversation_id,
                user_id,
                exc,
            )
            return False

    async def update_system_message_in_temp_context(
        self,
        user_id: str,
        conversation_id: str,
        agent_resp_id: str,
        new_system_content: Any,
    ) -> bool:
        """
        Update system message content in temp context (used for iteration counter).

        Args:
            new_system_content: New system message content (list of content items)
        Returns:
            True if updated successfully
        """
        try:
            key = self._temp_context_key(user_id, conversation_id, agent_resp_id)
            system_msg_id = "__system__"

            raw_payload = await self.redis.hget(key, system_msg_id)
            if not raw_payload:
                logger.warning(
                    f"System message not found in temp context for agent_resp {agent_resp_id}"
                )
                return False

            payload = json.loads(raw_payload)
            message_body = payload.get("message", {})
            message_body["content"] = new_system_content
            payload["message"] = message_body

            await self.redis.hset(key, mapping={system_msg_id: json.dumps(payload)})
            return True

        except Exception as exc:
            logger.error(
                f"Error updating system message in temp context for agent_resp {agent_resp_id}: {exc}"
            )
            return False
