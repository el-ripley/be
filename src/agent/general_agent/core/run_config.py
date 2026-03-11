"""Run configuration for agent execution."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.agent.common.conversation_settings import (
    DEFAULT_CONTEXT_BUFFER_PERCENT,
    DEFAULT_CONTEXT_TOKEN_LIMIT,
)
from src.agent.core.llm_call import LLM_call


@dataclass
class RunConfig:
    user_id: str
    conversation_id: str
    api_key: str
    settings: Dict[str, Any]
    model: str
    llm_call: LLM_call
    active_tab: Optional[Dict[str, Any]] = None
    # Context token management - should always be provided from get_effective_context_settings()
    # Defaults below are only for type safety, but should never be used in practice
    context_token_limit: int = DEFAULT_CONTEXT_TOKEN_LIMIT
    context_buffer_percent: int = DEFAULT_CONTEXT_BUFFER_PERCENT
    current_context_tokens: int = 0  # From latest openai_response.input_tokens
