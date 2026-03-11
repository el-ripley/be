"""Unit tests for AuthService (sync methods)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.database.postgres.entities.user_entities import Role, User
from src.services.auth_service import AuthService


@pytest.fixture
def mock_settings() -> None:
    with patch("src.services.auth_service.settings") as m:
        m.jwt_secret_key = "test-secret-key-min-32-characters-long"
        m.jwt_algorithm = "HS256"
        m.access_token_expire_minutes = 15
        m.refresh_token_expire_days = 30
        yield m


@pytest.fixture
def auth_service(mock_settings: None) -> AuthService:
    return AuthService()


@pytest.fixture
def sample_user() -> User:
    return User(
        id="user-123",
        created_at=1000,
        updated_at=2000,
        roles=[Role(id="r1", name="admin")],
    )


def test_create_access_token(auth_service: AuthService, sample_user: User) -> None:
    token = auth_service.create_access_token(sample_user)
    assert isinstance(token, str)
    assert len(token) > 0
    payload = auth_service.validate_token(token, "access")
    assert payload is not None
    assert payload.get("sub") == "user-123"
    assert payload.get("type") == "access"
    assert payload.get("roles") == ["admin"]


def test_create_refresh_token_jwt(auth_service: AuthService, sample_user: User) -> None:
    token = auth_service.create_refresh_token_jwt(sample_user)
    assert isinstance(token, str)
    payload = auth_service.validate_token(token, "refresh")
    assert payload is not None
    assert payload.get("sub") == "user-123"
    assert payload.get("type") == "refresh"


def test_validate_token_wrong_type_returns_none(
    auth_service: AuthService, sample_user: User
) -> None:
    access_token = auth_service.create_access_token(sample_user)
    payload = auth_service.validate_token(access_token, "refresh")
    assert payload is None


def test_validate_token_invalid_returns_none(auth_service: AuthService) -> None:
    payload = auth_service.validate_token("invalid.jwt.here", "access")
    assert payload is None


def test_get_user_from_token(auth_service: AuthService, sample_user: User) -> None:
    token = auth_service.create_access_token(sample_user)
    user = auth_service.get_user_from_token(token)
    assert user is not None
    assert user.get("id") == "user-123"
    assert "admin" in (user.get("roles") or [])


def test_get_user_from_token_invalid_returns_none(auth_service: AuthService) -> None:
    assert auth_service.get_user_from_token("bad-token") is None


def test_create_access_token_user_without_roles(auth_service: AuthService) -> None:
    user = User(id="u2", created_at=0, updated_at=0, roles=None)
    token = auth_service.create_access_token(user)
    payload = auth_service.validate_token(token, "access")
    assert payload is not None
    assert payload.get("roles") == []
