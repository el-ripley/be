"""Message accumulator for suggest_response_agent.

Keeps call/output order: tool outputs are inserted right after their function_call
so that to_sorted_messages() returns reasoning → call → output → call → output.
"""

from typing import Dict, List, Optional

from src.api.openai_conversations.schemas import MessageResponse


class SuggestResponseMessageAccumulator:
    """Accumulator for suggest_response that preserves call→output order."""

    def __init__(self) -> None:
        self.temp_messages_map: Dict[str, MessageResponse] = {}
        self._order: List[str] = []
        self.item_id_to_uuid_map: Dict[str, str] = {}
        self.item_id_to_type_map: Dict[str, str] = {}

    def get_message_uuid(self, item_id: str) -> Optional[str]:
        return self.item_id_to_uuid_map.get(item_id)

    def set_message_uuid(self, item_id: str, msg_uuid: str) -> None:
        self.item_id_to_uuid_map[item_id] = msg_uuid

    def set_message_type(self, item_id: str, msg_type: str) -> None:
        self.item_id_to_type_map[item_id] = msg_type

    def get_message_type(
        self, item_id: str, default: Optional[str] = None
    ) -> Optional[str]:
        return self.item_id_to_type_map.get(item_id, default)

    def store_message(self, message: MessageResponse) -> None:
        self.temp_messages_map[message.id] = message
        if message.id not in self._order:
            self._order.append(message.id)

    def insert_after_position(self, position: int, message: MessageResponse) -> None:
        """Insert message right after the item at position (tool output after its call)."""
        self.temp_messages_map[message.id] = message
        self._order.insert(position + 1, message.id)

    def to_sorted_messages(self) -> List[MessageResponse]:
        return [
            self.temp_messages_map[oid]
            for oid in self._order
            if oid in self.temp_messages_map
        ]
