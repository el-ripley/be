"""Run configuration dataclasses for suggest_response agent."""

from dataclasses import dataclass
from typing import Any, Dict, List

from src.api.openai_conversations.schemas import MessageResponse


@dataclass
class PreparedContext:
    """Context prepared for LLM call."""

    input_messages: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    settings: Dict[str, Any]
    num_suggestions: int
    user_id: str
    fan_page_id: str
    api_key: str


@dataclass
class LLMResult:
    """Result from LLM call."""

    suggestions_list: List[Dict[str, Any]]
    response_data: Dict[str, Any]
    latency_ms: int
    accumulated_messages: List[MessageResponse]
