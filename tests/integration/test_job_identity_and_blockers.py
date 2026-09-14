"""F3: job identity in lists and root-cause blockers on the detail endpoint.

Contract: docs/JOB_STATE_MACHINE.md §身份投影与字幕归因, docs/MCP_TOOLS.md §2
(blocked_reason/blocked_message). The blocker derives from the same gate the
worker applies — settings row plus bounded keyring existence, never network.
"""

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.storage.models import Job
from evoblue_video_mcp.storage.repository import enqueue_job, save_app_settings
from evoblue_video_mcp.web import create_app

_NOW = 1_700_000_000.0
_TOKEN = "identity-token"


class _MemoryCredentials:
    def __init__(self, secret: str | None) -> None:
        self.secret = secret

    def get_secret(self, reference: str) -> str | None:
        return self.secret

    def set_secret(self, reference: str, secret: str) -> None:
        self.secret = secret

    def delete_secret(self, reference: str) -> None:
        self.secret = None


async def _enqueue(
    session: AsyncSession,
    *,
    job_id: str,
    status: JobStatus = JobStatus.QUEUED,
) -> None:
    await enqueue_job(
        session,
        job_id=job_id,
        url="https://www.youtube.com/watch?v=abc",
        request_fingerprint=f"key-{job_id}",
        config_fingerprint="fp-1",
        now=_NOW,
        status=status,
    )


def _client(
    session_factory: async_sessionmaker[AsyncSession], store, port: int = 8765
) -> httpx.AsyncClient:  # type: ignore[type-arg]
    from evoblue_video_mcp.config import Settings

    app = create_app(
        settings=Settings(engine_port=port),
        session_factory=session_factory,
        local_token=_TOKEN,
        credential_store=store,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_list_shows_url_fallback_then_title(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess, job_id="j-early")
        await _enqueue(sess, job_id="j-late")
        await sess.execute(
            update(Job)
            .where(Job.job_id == "j-late")
            .values(title="标题已回填", platform="bilibili")
        )
        await sess.commit()

    async with _client(session_factory, _MemoryCredentials(None)) as client:
        r = await client.get("/api/jobs", headers={"X-Local-Token": _TOKEN})
    assert r.status_code == 200
    by_id = {item["job_id"]: item for item in r.json()["items"]}
    assert by_id["j-early"]["title"] is None
    assert by_id["j-early"]["url"] == "https://www.youtube.com/watch?v=abc"
    assert by_id["j-late"]["title"] == "标题已回填"
    assert by_id["j-late"]["platform"] == "bilibili"


async def test_queued_detail_reports_setup_blocker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess, job_id="j-q")

    async with _client(session_factory, _MemoryCredentials(None)) as client:
        r = await client.get("/api/jobs/j-q", headers={"X-Local-Token": _TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body["blocked_reason"] == "setup_incomplete"
    assert "/settings" in body["blocked_message"]
    assert "token" not in body["blocked_message"].lower()


async def test_queued_detail_reports_missing_key_when_settings_complete(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess, job_id="j-q2")
        await save_app_settings(
            sess,
            setup_completed=True,
            now=_NOW,
            llm_provider="deepseek",
            llm_base_url="https://api.deepseek.com",
            llm_model="deepseek-flash",
            llm_credential_ref="llm:deepseek",
        )

    async with _client(session_factory, _MemoryCredentials(None)) as client:
        r = await client.get("/api/jobs/j-q2", headers={"X-Local-Token": _TOKEN})
    assert r.json()["blocked_reason"] == "llm_key_unavailable"

    async with _client(session_factory, _MemoryCredentials("sk-ok")) as client:
        r = await client.get("/api/jobs/j-q2", headers={"X-Local-Token": _TOKEN})
    assert r.json()["blocked_reason"] is None


async def test_waiting_for_model_detail_points_at_models_page(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess, job_id="j-w", status=JobStatus.WAITING_FOR_MODEL)
        await sess.execute(
            update(Job)
            .where(Job.job_id == "j-w")
            .values(asr_recommendation_model_id="sensevoice-small-int8")
        )
        await sess.commit()

    async with _client(session_factory, _MemoryCredentials(None), port=9123) as client:
        r = await client.get("/api/jobs/j-w", headers={"X-Local-Token": _TOKEN})
    body = r.json()
    assert body["blocked_reason"] == "waiting_for_model"
    assert "sensevoice-small-int8" in body["blocked_message"]
    assert "http://127.0.0.1:9123/models" in body["blocked_message"]
    assert "token" not in body["blocked_message"].lower()


async def test_terminal_and_running_jobs_have_no_blocker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess, job_id="j-done", status=JobStatus.COMPLETED)
        await _enqueue(sess, job_id="j-run", status=JobStatus.TRANSCRIBING)

    async with _client(session_factory, _MemoryCredentials(None)) as client:
        for job_id in ("j-done", "j-run"):
            r = await client.get(f"/api/jobs/{job_id}", headers={"X-Local-Token": _TOKEN})
            assert r.json()["blocked_reason"] is None
