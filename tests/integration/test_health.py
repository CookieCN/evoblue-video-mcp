from fastapi.testclient import TestClient

import evoblue_video_mcp
from evoblue_video_mcp.web import create_app


def test_health_endpoint() -> None:
    response = TestClient(create_app()).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "EvoBlue Video MCP",
        # /api/health must serve the package's single version source of truth,
        # never a second stale literal (P7-a version-consistency gate).
        "version": evoblue_video_mcp.__version__,
    }

