from typing import Any, Dict, List, Literal, Optional, Tuple, TypedDict, Union

from src.api.openai_conversations.schemas import MessageResponse

from .image_processor import ImageProcessor


class OpenAIChatMessage(TypedDict):
    role: Literal["user", "assistant", "system", "developer"]
    content: Any


class OpenAIReasoning(TypedDict):
    type: Literal["reasoning"]
    summary: List[Dict[str, Any]]


class OpenAIFunctionCall(TypedDict):
    type: Literal["function_call"]
    call_id: Optional[str]
    name: Optional[str]
    arguments: str


class OpenAIFunctionCallOutput(TypedDict):
    type: Literal["function_call_output"]
    call_id: Optional[str]
    output: Union[str, List[Dict[str, Any]]]


class OpenAIWebSearchCall(TypedDict):
    type: Literal["web_search_call"]
    action: Dict[str, Any]


OpenAIMessageItem = Union[
    OpenAIChatMessage,
    OpenAIReasoning,
    OpenAIFunctionCall,
    OpenAIFunctionCallOutput,
    OpenAIWebSearchCall,
]


class MessageConverter:
    """Convert MessageResponse objects into OpenAI Response API message items.

    This class encapsulates:
    - type-based payload selection (message, reasoning, function_call, etc.)
    - normalization of reasoning summaries
    - role/content normalization for standard messages
    - delegating image handling to ImageProcessor
    """

    def __init__(self, image_processor: Optional[ImageProcessor] = None) -> None:
        self.image_processor = image_processor or ImageProcessor()

    async def convert_message(
        self,
        msg: MessageResponse,
        *,
        with_ids: bool = False,
        expiration_map: Optional[Dict[str, Optional[int]]] = None,
        media_id_map: Optional[Dict[str, Optional[str]]] = None,
    ) -> Optional[Union[OpenAIMessageItem, Tuple[str, OpenAIMessageItem]]]:
        """Public entrypoint mirroring
        AgentMessagesService._convert_message_response_to_openai_item.
        """
        msg_type = (msg.type or "message").lower()

        if msg_type in {"message", "user_input"}:
            payload = await self._build_standard_message_item(
                msg,
                expiration_map=expiration_map,
                media_id_map=media_id_map,
            )

        elif msg_type == "reasoning":
            summary_payload = self._get_effective_value(
                msg, "reasoning_summary", "modified_reasoning_summary"
            )
            normalized_summary = self._normalize_reasoning_summary(summary_payload)
            if not normalized_summary:
                return None
            payload = OpenAIReasoning(type="reasoning", summary=normalized_summary)

        elif msg_type == "function_call":
            from src.agent.utils import stringify_json_payload

            arguments_payload = self._get_effective_value(
                msg, "function_arguments", "modified_function_arguments"
            )
            payload = OpenAIFunctionCall(
                type="function_call",
                call_id=msg.call_id,
                name=msg.function_name,
                arguments=stringify_json_payload(arguments_payload),
            )

        elif msg_type == "function_call_output":
            from src.agent.general_agent.context.function_output_normalizer import (
                normalize_function_output_to_api_format,
            )
            from src.agent.utils import stringify_json_payload

            output_payload = self._get_effective_value(
                msg, "function_output", "modified_function_output"
            )
            if isinstance(output_payload, list):
                output_value = normalize_function_output_to_api_format(output_payload)
            else:
                output_value = stringify_json_payload(output_payload)
            payload = OpenAIFunctionCallOutput(
                type="function_call_output",
                call_id=msg.call_id,
                output=output_value,
            )

        elif msg_type == "web_search_call":
            payload = OpenAIWebSearchCall(
                type="web_search_call",
                action=msg.web_search_action or {},
            )

        elif msg_type == "summary":
            # Summary messages are treated as assistant messages
            payload = await self._build_standard_message_item(
                msg,
                expiration_map=expiration_map,
                media_id_map=media_id_map,
            )

        else:
            # Fallback to standard message behavior
            payload = await self._build_standard_message_item(
                msg,
                expiration_map=expiration_map,
                media_id_map=media_id_map,
            )

        if with_ids:
            return (msg.id, payload)
        return payload

    async def _build_standard_message_item(
        self,
        msg: MessageResponse,
        *,
        expiration_map: Optional[Dict[str, Optional[int]]] = None,
        media_id_map: Optional[Dict[str, Optional[str]]] = None,
    ) -> OpenAIChatMessage:
        """Mirror of AgentMessagesService._build_standard_message_item."""
        from src.agent.utils import stringify_content

        allowed_roles = {"user", "assistant", "system", "developer"}
        raw_role = msg.role or "user"
        normalized_role = raw_role if raw_role in allowed_roles else "assistant"

        content_payload = self._get_effective_value(msg, "content", "modified_content")
        if content_payload is None:
            content_payload = ""

        if raw_role not in allowed_roles:
            if raw_role == "tool":
                call_id = msg.call_id
                tool_output = (
                    self._get_effective_value(
                        msg, "function_output", "modified_function_output"
                    )
                    or content_payload
                )
                tool_output_str = stringify_content(tool_output)
                prefix = "[Tool Output]"
                if call_id:
                    prefix = f"{prefix} (call_id={call_id})"
                content_payload = f"{prefix}\n{tool_output_str}"
            else:
                content_payload = f"[{raw_role}] {stringify_content(content_payload)}"

        normalized_content = await self.image_processor.process_content_with_images(
            normalized_role=normalized_role,
            content_payload=content_payload,
            expiration_map=expiration_map,
            media_id_map=media_id_map,
        )

        return OpenAIChatMessage(role=normalized_role, content=normalized_content)

    @staticmethod
    def _normalize_reasoning_summary(summary_payload: Any) -> List[Dict[str, Any]]:
        from src.agent.utils import ensure_list

        normalized_items: List[Dict[str, Any]] = []

        for entry in ensure_list(summary_payload):
            if isinstance(entry, dict):
                text_value = entry.get("text")
                if text_value is None:
                    continue
                entry_type = entry.get("type") or "summary_text"
                normalized_items.append({"text": str(text_value), "type": entry_type})
            elif isinstance(entry, str):
                normalized_items.append({"text": entry, "type": "summary_text"})

        return normalized_items

    @staticmethod
    def _get_effective_value(
        msg: MessageResponse, attr_name: str, modified_attr_name: Optional[str]
    ) -> Any:
        if modified_attr_name and msg.is_modified:
            modified_value = getattr(msg, modified_attr_name, None)
            if modified_value is not None:
                return modified_value
        return getattr(msg, attr_name, None)


__all__ = [
    "MessageConverter",
    "OpenAIChatMessage",
    "OpenAIReasoning",
    "OpenAIFunctionCall",
    "OpenAIFunctionCallOutput",
    "OpenAIWebSearchCall",
    "OpenAIMessageItem",
]
