"""Unit tests for estimate_context_tokens."""

import pytest

from src.utils.estimate_context_tokens_o200k_base import estimate_context_tokens


def test_empty_messages_zero() -> None:
    assert estimate_context_tokens([]) == 0


def test_single_message_type_message() -> None:
    messages = [{"type": "message", "content": "Hello"}]
    n = estimate_context_tokens(messages)
    assert n >= 1
    assert isinstance(n, int)


def test_single_message_simple_content() -> None:
    messages = [{"content": "Hi"}]
    n = estimate_context_tokens(messages)
    assert n >= 1


def test_reasoning_type_with_summary() -> None:
    messages = [
        {
            "type": "reasoning",
            "summary": [{"text": "Step one"}],
        }
    ]
    n = estimate_context_tokens(messages)
    assert n >= 1


def test_function_call_type() -> None:
    messages = [
        {
            "type": "function_call",
            "name": "get_weather",
            "arguments": '{"city": "NYC"}',
        }
    ]
    n = estimate_context_tokens(messages)
    assert n >= 1
