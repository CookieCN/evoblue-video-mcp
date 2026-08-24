from fastapi.testclient import TestClient

from evoblue_video_mcp.web import create_app


def test_health_endpoint() -> None:
    response = TestClient(create_app()).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "EvoBlue Video MCP",
        "version": "0.1.0",
    }

