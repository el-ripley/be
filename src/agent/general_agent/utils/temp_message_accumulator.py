from typing import Dict, List, Optional

from src.api.openai_conversations.schemas import MessageResponse


class TempMessageAccumulator:
    """Holds temporary streaming messages and id mappings during an iteration."""

    def __init__(self) -> None:
        self.temp_messages_map: Dict[str, MessageResponse] = {}
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

    def to_sorted_messages(self) -> List[MessageResponse]:
        return sorted(self.temp_messages_map.values(), key=lambda x: x.created_at)
