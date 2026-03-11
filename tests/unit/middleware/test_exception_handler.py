"""Unit tests for exception handlers and ErrorResponse."""

import pytest
from unittest.mock import MagicMock
from fastapi import Request
from fastapi.exceptions import RequestValidationError
import jwt
import asyncpg

from src.middleware.exception_handler import (
    ErrorResponse,
    BusinessLogicError,
    ExternalServiceError,
    http_exception_handler,
    validation_exception_handler,
    jwt_exception_handler,
    database_exception_handler,
    general_exception_handler,
    business_logic_exception_handler,
    external_service_exception_handler,
    is_database_exception,
)


@pytest.fixture
def mock_request() -> Request:
    req = MagicMock(spec=Request)
    req.url = MagicMock(path="/test")
    return req


@pytest.mark.asyncio
async def test_business_logic_error_returns_400(mock_request: Request) -> None:
    exc = BusinessLogicError("Invalid state", details={"field": "value"})
    response = await business_logic_exception_handler(mock_request, exc)
    assert response.status_code == 400
    body = response.body.decode()
    assert "business_logic_error" in body
    assert "Invalid state" in body
    assert "field" in body


@pytest.mark.asyncio
async def test_external_service_error_returns_503(mock_request: Request) -> None:
    exc = ExternalServiceError("Facebook", "Rate limited", status_code=503)
    response = await external_service_exception_handler(mock_request, exc)
    assert response.status_code == 503
    body = response.body.decode()
    assert "external_service_error" in body
    assert "Facebook" in body


@pytest.mark.asyncio
async def test_jwt_expired_returns_401(mock_request: Request) -> None:
    exc = jwt.ExpiredSignatureError("Token expired")
    response = await jwt_exception_handler(mock_request, exc)
    assert response.status_code == 401
    body = response.body.decode()
    assert "expired" in body.lower() or "token" in body.lower()
    assert response.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_jwt_invalid_returns_401(mock_request: Request) -> None:
    exc = jwt.InvalidTokenError("Invalid")
    response = await jwt_exception_handler(mock_request, exc)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_postgres_error_returns_500(mock_request: Request) -> None:
    exc = asyncpg.PostgresError("syntax error at end of input")
    response = await database_exception_handler(mock_request, exc)
    assert response.status_code == 500
    body = response.body.decode()
    assert "database" in body.lower() or "error" in body.lower()


@pytest.mark.asyncio
async def test_validation_error_returns_422(mock_request: Request) -> None:
    exc = RequestValidationError(errors=[{"loc": ("body", "x"), "msg": "Missing", "type": "value_error"}])
    response = await validation_exception_handler(mock_request, exc)
    assert response.status_code == 422
    body = response.body.decode()
    assert "validation" in body.lower()
    assert "validation_errors" in body or "error" in body


@pytest.mark.asyncio
async def test_general_exception_returns_500_no_stack_leak(mock_request: Request) -> None:
    exc = RuntimeError("Internal failure")
    response = await general_exception_handler(mock_request, exc)
    assert response.status_code == 500
    body = response.body.decode()
    assert "unexpected" in body.lower() or "error" in body.lower()
    assert "Internal failure" not in body
    assert "Traceback" not in body


@pytest.mark.asyncio
async def test_http_exception_preserves_status(mock_request: Request) -> None:
    from fastapi import HTTPException
    exc = HTTPException(status_code=404, detail="Not found")
    response = await http_exception_handler(mock_request, exc)
    assert response.status_code == 404
    body = response.body.decode()
    assert "Not found" in body


def test_error_response_create() -> None:
    out = ErrorResponse.create("test_type", "Test message", status_code=400)
    assert out["error"]["type"] == "test_type"
    assert out["error"]["message"] == "Test message"
    assert out["error"]["status_code"] == 400


def test_error_response_create_with_details() -> None:
    out = ErrorResponse.create("t", "m", details={"key": "value"}, status_code=422)
    assert out["error"]["details"] == {"key": "value"}


def test_is_database_exception_postgres() -> None:
    assert is_database_exception(asyncpg.PostgresError("x")) is True


def test_is_database_exception_connection() -> None:
    assert is_database_exception(asyncpg.ConnectionDoesNotExistError()) is True


def test_is_database_exception_generic_with_connection() -> None:
    assert is_database_exception(ValueError("connection refused")) is True


def test_is_database_exception_generic_with_database() -> None:
    assert is_database_exception(ValueError("database locked")) is True


def test_is_database_exception_false() -> None:
    assert is_database_exception(ValueError("other")) is False
