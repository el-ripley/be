from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping
from uuid import UUID


def to_serializable(value: Any) -> Any:
    """
    Recursively convert supported types (e.g. UUID, set) into JSON-serializable structures.
    """
    if value is None:
        return None

    if is_dataclass(value):
        return to_serializable(asdict(value))

    if hasattr(value, "model_dump"):
        try:
            return to_serializable(value.model_dump(mode="json"))
        except TypeError:
            # Fallback to dict conversion if model_dump signature differs
            return to_serializable(value.model_dump())

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, Mapping):
        result = {}
        for key, val in value.items():
            if isinstance(key, UUID):
                key_serialized = str(key)
            else:
                key_serialized = key
            result[key_serialized] = to_serializable(val)
        return result

    if isinstance(value, tuple):
        return [to_serializable(item) for item in value]

    if isinstance(value, list):
        return [to_serializable(item) for item in value]

    if isinstance(value, set):
        return [to_serializable(item) for item in value]

    return value
