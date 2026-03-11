"""Unit tests for to_serializable."""

from dataclasses import dataclass
from uuid import UUID

import pytest
from pydantic import BaseModel

from src.utils.serialization import to_serializable


def test_none() -> None:
    assert to_serializable(None) is None


def test_uuid() -> None:
    u = UUID("550e8400-e29b-41d4-a716-446655440000")
    assert to_serializable(u) == "550e8400-e29b-41d4-a716-446655440000"


def test_dict_with_uuid_value() -> None:
    u = UUID("550e8400-e29b-41d4-a716-446655440000")
    assert to_serializable({"key": u}) == {
        "key": "550e8400-e29b-41d4-a716-446655440000"
    }


def test_list_with_uuid() -> None:
    u = UUID("550e8400-e29b-41d4-a716-446655440000")
    assert to_serializable([u, 1]) == ["550e8400-e29b-41d4-a716-446655440000", 1]


def test_set_converted_to_list() -> None:
    out = to_serializable({1, 2, 3})
    assert isinstance(out, list)
    assert set(out) == {1, 2, 3}


def test_nested_dict() -> None:
    u = UUID("550e8400-e29b-41d4-a716-446655440000")
    out = to_serializable({"a": {"b": u}})
    assert out == {"a": {"b": "550e8400-e29b-41d4-a716-446655440000"}}


def test_tuple_converted_to_list() -> None:
    out = to_serializable((1, 2))
    assert out == [1, 2]


@dataclass
class Foo:
    x: int
    y: str


def test_dataclass() -> None:
    out = to_serializable(Foo(1, "a"))
    assert out == {"x": 1, "y": "a"}


class PydanticModel(BaseModel):
    id: UUID
    name: str


def test_pydantic_model() -> None:
    m = PydanticModel(id=UUID("550e8400-e29b-41d4-a716-446655440000"), name="test")
    out = to_serializable(m)
    assert out["id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert out["name"] == "test"


def test_primitive_passthrough() -> None:
    assert to_serializable(42) == 42
    assert to_serializable("hello") == "hello"
    assert to_serializable(3.14) == 3.14
    assert to_serializable(True) is True
