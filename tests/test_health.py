"""Health endpoint tests."""

from starlette.testclient import TestClient

from radar.api import app


def test_health() -> None:
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
