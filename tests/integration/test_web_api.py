"""Local Engine HTTP API: jobs observation and first-setup settings."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.config import Settings
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.storage.repository import enqueue_job
from evoblue_video_mcp.web import create_app


class _MemoryCredentials:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_secret(self, reference: str) -> str | None:
        return self.values.get(reference)

    def set_secret(self, reference: str, secret: str) -> None:
        self.values[reference] = secret

    def delete_secret(self, reference: str) -> None:
        self.values.pop(reference, None)


async def _enqueue(
    session: AsyncSession, *, job_id: str, status: JobStatus = JobStatus.QUEUED
) -> None:
    await enqueue_job(
        session,
        job_id=job_id,
        url="https://www.youtube.com/watch?v=abc",
        request_fingerprint=f"key-{job_id}",
        config_fingerprint="fp-1",
        now=1000.0,
        status=status,
    )


def _client(session_factory: async_sessionmaker[AsyncSession]) -> httpx.AsyncClient:
    app = create_app(session_factory=session_factory)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_endpoint_without_session() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_jobs_list_and_detail(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess, job_id="j1")
        await _enqueue(sess, job_id="j2", status=JobStatus.COMPLETED)

    async with _client(session_factory) as client:
        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

        resp = await client.get("/api/jobs", params={"status": "completed"})
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["job_id"] == "j2"

        resp = await client.get("/api/jobs/j1")
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "j1"
        assert resp.json()["status"] == JobStatus.QUEUED.value

        resp = await client.get("/api/jobs/missing")
        assert resp.status_code == 404


async def test_jobs_rejects_out_of_range_pagination(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        assert (await client.get("/api/jobs", params={"limit": 0})).status_code == 422
        assert (await client.get("/api/jobs", params={"limit": 101})).status_code == 422
        assert (await client.get("/api/jobs", params={"offset": -1})).status_code == 422


async def test_cancel_queued_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess, job_id="cancel-me")

    async with _client(session_factory) as client:
        resp = await client.post("/api/jobs/cancel-me/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == JobStatus.CANCELLED.value
    assert resp.json()["error_code"] == "CANCELLED_BY_USER"


async def test_settings_roundtrip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        resp = await client.get("/api/settings")
        assert resp.status_code == 200
        assert resp.json()["setup_completed"] is False

        resp = await client.put(
            "/api/settings",
            json={
                "setup_completed": True,
                "report_directory": "C:/reports",
                "llm_provider": "openai",
                "llm_model": "gpt-5",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["setup_completed"] is True
        assert resp.json()["report_directory"] == "C:/reports"
        assert resp.json()["llm_provider"] == "openai"
        assert resp.json()["llm_model"] == "gpt-5"

        resp = await client.get("/api/settings")
        assert resp.json()["setup_completed"] is True
        assert resp.json()["report_directory"] == "C:/reports"


async def test_settings_reject_unknown_asr_provider(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # A bogus provider must be refused at the boundary: once persisted it would
    # fail every ASR job that routes through the saved preference.
    async with _client(session_factory) as client:
        resp = await client.put(
            "/api/settings", json={"asr_provider": "totally-real-asr"}
        )
    assert resp.status_code == 422


async def test_settings_accept_known_asr_provider(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        resp = await client.put(
            "/api/settings", json={"asr_provider": "whisper-cpp-base"}
        )
        assert resp.status_code == 200
        assert resp.json()["asr_provider"] == "whisper-cpp-base"

        resp = await client.put("/api/settings", json={"asr_provider": "auto"})
        assert resp.status_code == 200
        assert resp.json()["asr_provider"] == "auto"


async def test_settings_explicit_null_clears_field(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        await client.put(
            "/api/settings",
            json={"setup_completed": True, "report_directory": "C:/reports"},
        )

        # Explicit null clears the field; omitted fields keep their old value.
        await client.put("/api/settings", json={"report_directory": None})
        resp = await client.get("/api/settings")
        assert resp.json()["report_directory"] is None
        assert resp.json()["setup_completed"] is True


async def test_settings_store_api_key_only_in_credential_store(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            "/api/settings",
            json={
                "setup_completed": True,
                "llm_provider": "openai",
                "llm_base_url": "https://api.openai.com/v1/",
                "llm_model": "gpt-test",
                "llm_api_key": "super-secret-key",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["llm_base_url"] == "https://api.openai.com/v1"
        assert resp.json()["llm_api_key_configured"] is True
        assert "super-secret-key" not in resp.text

        fetched = await client.get("/api/settings")
        assert fetched.json()["llm_api_key_configured"] is True
        assert "super-secret-key" not in fetched.text

    assert credentials.values == {"llm:openai": "super-secret-key"}


async def test_settings_reject_insecure_remote_llm_url(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        resp = await client.put(
            "/api/settings", json={"llm_base_url": "http://api.example.com/v1"}
        )
    assert resp.status_code == 422


async def test_local_token_protects_data_endpoints(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess, job_id="j1")

    app = create_app(session_factory=session_factory, local_token="secret-token")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Data endpoints require the token.
        assert (await client.get("/api/jobs")).status_code == 401
        assert (
            await client.get("/api/jobs", headers={"X-Local-Token": "wrong"})
        ).status_code == 401
        assert (
            await client.get("/api/jobs", headers={"X-Local-Token": "secret-token"})
        ).status_code == 200

        # Health stays token-exempt.
        assert (await client.get("/api/health")).status_code == 200


def test_production_requires_local_token() -> None:
    settings = Settings(environment="production", local_access_token="")
    with pytest.raises(RuntimeError):
        create_app(settings=settings)


async def test_submit_job_endpoint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        resp = await client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
        assert resp.status_code == 200
        first = resp.json()
        assert first["job_id"]
        assert first["reused"] is False

        second = await client.post(
            "/api/jobs", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        )
        assert second.json()["reused"] is True
        assert second.json()["job_id"] == first["job_id"]


async def test_submit_job_rejects_invalid_url(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        resp = await client.post("/api/jobs", json={"url": "not-a-url"})
        assert resp.status_code == 422
