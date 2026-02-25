"""Shared defaults and helpers for per-user agent settings."""

from typing import Any

MIN_NUM_SUGGESTIONS = 1
MAX_NUM_SUGGESTIONS = 5
DEFAULT_NUM_SUGGESTIONS = 3


def validate_num_suggestions(value: Any) -> int:
    """Validate that num_suggestions stays within configured bounds."""
    try:
        num_value = int(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - simple guard
        raise ValueError(
            f"num_suggestions must be an integer between {MIN_NUM_SUGGESTIONS} and {MAX_NUM_SUGGESTIONS}"
        ) from exc

    if num_value < MIN_NUM_SUGGESTIONS or num_value > MAX_NUM_SUGGESTIONS:
        raise ValueError(
            f"num_suggestions must be between {MIN_NUM_SUGGESTIONS} and {MAX_NUM_SUGGESTIONS}"
        )

    return num_value


def coerce_num_suggestions(value: Any) -> int:
    """Best-effort conversion; falls back to default when invalid."""
    try:
        return validate_num_suggestions(value)
    except ValueError:
        return DEFAULT_NUM_SUGGESTIONS


__all__ = [
    "MIN_NUM_SUGGESTIONS",
    "MAX_NUM_SUGGESTIONS",
    "DEFAULT_NUM_SUGGESTIONS",
    "validate_num_suggestions",
    "coerce_num_suggestions",
]
