"""
Conversation settings management for OpenAI conversations.

Handles parsing, validation, and default settings for conversation-level
model configuration (model, reasoning, verbosity).
"""

import re
from typing import Any, Dict, Optional

import asyncpg

# Supported models
SUPPORTED_MODELS = ["gpt-5-mini", "gpt-5-nano", "gpt-5", "gpt-5.2"]
MODEL_GPT_5_2 = "gpt-5.2"

# Valid reasoning efforts
REASONING_EFFORTS = ["low", "medium", "high", "none"]
REASONING_NONE = "none"

# Valid verbosity levels
VERBOSITY_LEVELS = ["low", "medium", "high"]

# ============================================================================
# CONTEXT TOKEN MANAGEMENT CONFIG - SINGLE SOURCE OF TRUTH
# ============================================================================
# Change these values to adjust token limits system-wide.
# All other files must import and use these constants.
# ============================================================================

DEFAULT_CONTEXT_TOKEN_LIMIT = 200000
DEFAULT_CONTEXT_BUFFER_PERCENT = 20  # Percentage of limit reserved as buffer
DEFAULT_SUMMARIZER_MODEL = "gpt-5-nano"
DEFAULT_VISION_MODEL = "gpt-5-nano"

# ============================================================================

# Default: gpt-5.2, low verbosity, low reasoning (verbosity for 5.2 has no "none", use low)
DEFAULT_SETTINGS: Dict[str, Any] = {
    "model": "gpt-5.2",
    "reasoning": "low",
    "verbosity": "low",
    "web_search_enabled": True,
}


def get_default_settings() -> Dict[str, Any]:
    """Get default conversation settings."""
    return dict(DEFAULT_SETTINGS)


async def get_effective_context_settings(
    user_id: str, conn: asyncpg.Connection
) -> Dict[str, Any]:
    """
    Get effective context settings by merging user settings with system defaults.

    Args:
        user_id: User ID to get settings for
        conn: Database connection

    Returns:
        Dict with merged settings (context_token_limit, context_buffer_percent,
        summarizer_model, vision_model)
    """
    from src.database.postgres.repositories.user_queries import (
        get_user_conversation_settings,
    )

    # Get user settings from DB
    user_settings_record = await get_user_conversation_settings(conn, user_id)

    # Start with system defaults
    effective_settings = {
        "context_token_limit": DEFAULT_CONTEXT_TOKEN_LIMIT,
        "context_buffer_percent": DEFAULT_CONTEXT_BUFFER_PERCENT,
        "summarizer_model": DEFAULT_SUMMARIZER_MODEL,
        "vision_model": DEFAULT_VISION_MODEL,
    }

    # Merge user settings if they exist
    if user_settings_record:
        if user_settings_record.get("context_token_limit") is not None:
            effective_settings["context_token_limit"] = user_settings_record[
                "context_token_limit"
            ]
        if user_settings_record.get("context_buffer_percent") is not None:
            effective_settings["context_buffer_percent"] = user_settings_record[
                "context_buffer_percent"
            ]
        if user_settings_record.get("summarizer_model"):
            effective_settings["summarizer_model"] = user_settings_record[
                "summarizer_model"
            ]
        if user_settings_record.get("vision_model"):
            effective_settings["vision_model"] = user_settings_record["vision_model"]

    return effective_settings


def validate_model(model: str) -> bool:
    """Validate that model is supported."""
    return model in SUPPORTED_MODELS


def validate_reasoning(reasoning: str) -> bool:
    """Validate that reasoning effort is valid."""
    return reasoning in REASONING_EFFORTS


def validate_verbosity(verbosity: str) -> bool:
    """Validate that verbosity level is valid."""
    return verbosity in VERBOSITY_LEVELS


def validate_settings(settings: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate conversation settings.

    Returns:
        (is_valid, error_message)
    """
    model = settings.get("model")
    reasoning = settings.get("reasoning")
    verbosity = settings.get("verbosity")

    if not model:
        return False, "model is required"

    if not validate_model(model):
        return (
            False,
            f"Invalid model: {model}. Supported models: {', '.join(SUPPORTED_MODELS)}",
        )

    if reasoning is not None:
        if not validate_reasoning(reasoning):
            return (
                False,
                f"Invalid reasoning: {reasoning}. Valid values: {', '.join(REASONING_EFFORTS)}",
            )

        # Only gpt-5.2 supports reasoning=none
        if reasoning == REASONING_NONE and model != MODEL_GPT_5_2:
            return False, f"reasoning='none' is only supported for {MODEL_GPT_5_2}"

    if verbosity is not None:
        if not validate_verbosity(verbosity):
            return (
                False,
                f"Invalid verbosity: {verbosity}. Valid values: {', '.join(VERBOSITY_LEVELS)}",
            )

    return True, None


def parse_settings_string(settings_str: str) -> Dict[str, Any]:
    """
    Parse settings string from API format.

    Format examples:
    - "gpt-5-mini reasoning: high, verbosity: high"
    - "gpt-5.2 reasoning: none, verbosity: medium"
    - "gpt-5-mini"

    Returns:
        Dict with model, reasoning, verbosity keys
    """
    settings: Dict[str, Any] = {}

    # Extract model (first word)
    model_match = re.match(r"^(\S+)", settings_str.strip())
    if not model_match:
        raise ValueError("Invalid settings format: model is required")

    model = model_match.group(1)
    if not validate_model(model):
        raise ValueError(f"Invalid model: {model}")

    settings["model"] = model

    # Extract reasoning and verbosity from remaining string
    remaining = settings_str[len(model) :].strip()

    # Parse reasoning
    reasoning_match = re.search(r"reasoning\s*:\s*(\w+)", remaining, re.IGNORECASE)
    if reasoning_match:
        reasoning = reasoning_match.group(1).lower()
        if not validate_reasoning(reasoning):
            raise ValueError(f"Invalid reasoning: {reasoning}")
        settings["reasoning"] = reasoning

    # Parse verbosity
    verbosity_match = re.search(r"verbosity\s*:\s*(\w+)", remaining, re.IGNORECASE)
    if verbosity_match:
        verbosity = verbosity_match.group(1).lower()
        if not validate_verbosity(verbosity):
            raise ValueError(f"Invalid verbosity: {verbosity}")
        settings["verbosity"] = verbosity

    # Validate the parsed settings
    is_valid, error = validate_settings(settings)
    if not is_valid:
        raise ValueError(f"Invalid settings: {error}")

    return settings


def normalize_settings(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Normalize settings by merging with defaults.

    Args:
        settings: Partial or complete settings dict

    Returns:
        Complete settings dict with defaults for missing keys
    """
    if not settings:
        return get_default_settings()

    normalized = dict(get_default_settings())
    normalized.update(settings)

    # Validate normalized settings
    is_valid, error = validate_settings(normalized)
    if not is_valid:
        raise ValueError(f"Invalid settings after normalization: {error}")

    return normalized


def get_reasoning_param(model: str, reasoning: str) -> Optional[Dict[str, Any]]:
    """
    Get reasoning parameter for LLM call.

    For gpt-5.2 with reasoning=none, returns None.
    For other cases, returns {"effort": reasoning, "summary": "auto"}.

    Returns:
        Reasoning dict or None
    """
    if model == MODEL_GPT_5_2 and reasoning == REASONING_NONE:
        return None

    return {"effort": reasoning, "summary": "auto"}


__all__ = [
    "SUPPORTED_MODELS",
    "MODEL_GPT_5_2",
    "REASONING_EFFORTS",
    "REASONING_NONE",
    "VERBOSITY_LEVELS",
    "DEFAULT_CONTEXT_TOKEN_LIMIT",
    "DEFAULT_CONTEXT_BUFFER_PERCENT",
    "DEFAULT_SUMMARIZER_MODEL",
    "DEFAULT_VISION_MODEL",
    "DEFAULT_SETTINGS",
    "get_default_settings",
    "get_effective_context_settings",
    "validate_model",
    "validate_reasoning",
    "validate_verbosity",
    "validate_settings",
    "parse_settings_string",
    "normalize_settings",
    "get_reasoning_param",
]
