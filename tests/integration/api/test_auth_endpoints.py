"""Integration tests for auth endpoints."""


def test_auth_refresh_without_token_returns_401(client) -> None:
    response = client.post("/auth/refresh")
    assert response.status_code == 401


def test_auth_refresh_with_empty_header_returns_401(client) -> None:
    response = client.post("/auth/refresh", headers={"X-Refresh-Token": ""})
    assert response.status_code == 401


def test_auth_logout_requires_auth(client) -> None:
    # Logout requires Bearer token (get_current_user_id); without it returns 401
    response = client.post("/auth/logout")
    assert response.status_code == 401
