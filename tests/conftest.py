"""Shared pytest fixtures and test configuration."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth.router import router as auth_router


# ----- Minimal test app for integration tests (no lifespan / no Redis-DB) -----

def _root() -> dict:
    """Mirror of main app root endpoint."""
    return {
        "message": "El Ripley AI Agent",
        "status": "ready",
        "endpoints": {
            "auth": "/facebook/auth/callback",
            "pages": "/facebook/page-admins",
            "send_message": "/facebook/messages/send",
            "conversations": "/facebook/messages/conversations",
            "mark_read_status": "/facebook/messages/conversations/{conversation_id}/mark-as-read",
            "comments": "/facebook/pages/{page_id}/posts/{post_id}/comments",
            "webhook": "/facebook/webhook",
            "user_info": "/users/me",
            "openai": "/openai",
        },
        "websocket": {
            "endpoint": "/socket.io/",
            "authentication": "JWT token required in auth object",
            "events": {
                "incoming": ["ai_message", "update_active_tab"],
                "outgoing": [
                    "connected",
                    "webhook_event",
                    "ai_response",
                    "ai_message_received",
                    "system_message",
                    "active_tab_updated",
                    "error",
                ],
            },
        },
    }


def _health_check() -> dict:
    """Mirror of main app health endpoint."""
    return {"status": "healthy", "message": "Facebook testing server is running"}


test_app = FastAPI()
test_app.add_api_route("/", _root, methods=["GET"])
test_app.add_api_route("/health", _health_check, methods=["GET"])
test_app.include_router(auth_router)


# ----- Fixtures -----


@pytest.fixture
def mock_auth_service() -> MagicMock:
    """AuthService mock for tests."""
    service = MagicMock()
    service.validate_token.return_value = {"user_id": "test-user-123", "sub": "test-user-123", "type": "access"}
    service.get_refresh_token_from_request.return_value = None
    service.get_access_token_from_request.return_value = None
    return service


@pytest.fixture
def mock_db_conn() -> AsyncMock:
    """Async PG connection mock for tests."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="OK")
    conn.close = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def client(mock_auth_service: MagicMock, mock_db_conn: AsyncMock) -> TestClient:
    """Test client for integration tests using minimal app (no lifespan)."""
    test_app.state.auth_service = mock_auth_service
    with patch(
        "src.api.auth.router.get_database_connection",
        new_callable=AsyncMock,
        return_value=mock_db_conn,
    ):
        with TestClient(test_app, raise_server_exceptions=False) as c:
            yield c
    if hasattr(test_app, "dependency_overrides"):
        test_app.dependency_overrides.clear()


@pytest.fixture
def app_client(client: TestClient) -> TestClient:
    """Alias for client for clarity in tests."""
    return client
