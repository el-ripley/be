"""Integration tests for root and health endpoints."""


def test_health_check(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "message" in data


def test_root_endpoint(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "endpoints" in data
    assert data["status"] == "ready"
    assert "message" in data
