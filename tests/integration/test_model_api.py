"""Model-management HTTP API: list, install, cancel, uninstall."""

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.asr.service import ModelManagerService
from evoblue_video_mcp.web import create_app


def _app(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> tuple[FastAPI, ModelManagerService]:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = ModelManagerService(
        models_dir=tmp_path, session_factory=session_factory, http_client=client
    )
    app = create_app(session_factory=session_factory, model_service=service)
    return app, service


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_models_list(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    app, service = _app(session_factory, tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/models")
    await service.close()

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {item["model_id"] for item in items} == {
        "sensevoice-small-int8",
        "whisper-cpp-base",
        "zipformer-ctc-small-zh-int8",
    }
    for item in items:
        assert item["tier"] in {"lite", "standard", "multilingual"}
        assert item["redistribution"] == "upstream_only"
        assert item["compressed_size_bytes"] > 0
        assert item["installed_size_bytes"] > 0
        assert item["installed"] is False
        assert item["active"] is False
        assert isinstance(item["formal_default"], bool)


async def test_unknown_model_returns_404(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    app, service = _app(session_factory, tmp_path)
    async with _client(app) as client:
        assert (await client.post("/api/models/unknown/install")).status_code == 404
        assert (await client.post("/api/models/unknown/cancel")).status_code == 404
        assert (await client.delete("/api/models/unknown")).status_code == 404
    await service.close()


async def test_uninstall_reclaims_zero_when_not_installed(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    app, service = _app(session_factory, tmp_path)
    async with _client(app) as client:
        resp = await client.delete("/api/models/sensevoice-small-int8")
    await service.close()

    assert resp.status_code == 200
    body = resp.json()
    assert body["reclaimed_bytes"] == 0
    assert body["pending_reclaim_bytes"] == 0


async def test_install_returns_202(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    app, service = _app(session_factory, tmp_path)
    async with _client(app) as client:
        resp = await client.post("/api/models/zipformer-ctc-small-zh-int8/install")
    await service.close()

    assert resp.status_code == 202
    assert resp.json()["model_id"] == "zipformer-ctc-small-zh-int8"
