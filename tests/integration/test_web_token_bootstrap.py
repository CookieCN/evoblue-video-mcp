"""P7-002: packaged-WebUI production behaviour (token gate + SPA deep links).

The production entry mounts the WebUI and registers the SPA catch-all; the
frozen=production rule makes data endpoints token-gated. These tests exercise
the mounted app directly: 401 without the header, 200 with it, /api/health
exempt, deep links served from index.html, real asset files served as-is, and
unknown /api/* paths still answering 404 instead of index.html.
"""

import sys
from pathlib import Path

import httpx
import pytest

from evoblue_video_mcp.__main__ import _mount_frontend
from evoblue_video_mcp.web import create_app


@pytest.fixture()
def spa_app(tmp_path: Path, monkeypatch, session_factory):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>evoblue-index</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1);", encoding="utf-8")
    # pin the dist root: the repo checkout has a real frontend/dist that would
    # otherwise win the candidate search from the runner cwd
    monkeypatch.setattr("evoblue_video_mcp.__main__._dist_root", lambda: dist)
    app = create_app(session_factory=session_factory, local_token="tok-123")
    _mount_frontend(app)
    return app


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_data_endpoint_rejects_missing_and_accepts_valid_token(spa_app) -> None:
    async with _client(spa_app) as client:
        assert (await client.get("/api/jobs")).status_code == 401
        ok = await client.get("/api/jobs", headers={"X-Local-Token": "tok-123"})
        assert ok.status_code == 200


async def test_health_stays_token_exempt(spa_app) -> None:
    async with _client(spa_app) as client:
        assert (await client.get("/api/health")).status_code == 200


async def test_spa_catchall_serves_index_for_deep_links(spa_app) -> None:
    async with _client(spa_app) as client:
        for path in ("/settings", "/mcp", "/models"):
            r = await client.get(path)
            assert r.status_code == 200, path
            assert "evoblue-index" in r.text, path


async def test_spa_catchall_serves_real_asset_files(spa_app) -> None:
    async with _client(spa_app) as client:
        r = await client.get("/assets/app.js")
        assert r.status_code == 200
        assert "console.log" in r.text


async def test_spa_catchall_rejects_traversal_and_unknown_api(spa_app) -> None:
    async with _client(spa_app) as client:
        r = await client.get("/api/unknown")
        assert r.status_code == 404
        # a traversal path must never leak a file outside the dist root; the
        # fail-safe behaviour is the index fallback (or a 4xx from proxies)
        traversal = await client.get("/..%2f..%2f..%2fpyproject.toml")
        body = traversal.text
        assert "[build-system]" not in body


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only file layout probe")
async def test_traversal_is_contained(spa_app) -> None:
    async with _client(spa_app) as client:
        r = await client.get("/../../pyproject.toml")
        assert "evoblue-index" in r.text or r.status_code in {404, 400}
