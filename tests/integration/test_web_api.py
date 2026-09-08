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
    app = create_app(session_factory=session_factory, credential_store=_MemoryCredentials())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/settings")
        assert resp.status_code == 200
        assert resp.json()["setup_completed"] is False

        # completing setup requires a runnable LLM configuration (P1 review)
        resp = await client.put(
            "/api/settings",
            json={
                "setup_completed": True,
                "report_directory": "C:/reports",
                "llm_provider": "openai",
                "llm_base_url": "https://api.openai.com/v1",
                "llm_model": "gpt-5",
                "llm_api_key": "sk-roundtrip",
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
    app = create_app(session_factory=session_factory, credential_store=_MemoryCredentials())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.put(
            "/api/settings",
            json={
                "setup_completed": True,
                "report_directory": "C:/reports",
                "llm_provider": "openai",
                "llm_base_url": "https://api.openai.com/v1",
                "llm_model": "gpt-5",
                "llm_api_key": "sk-null",
            },
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

async def test_cancel_survives_concurrent_writer_commit(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path,
    monkeypatch,
) -> None:
    """Review P1: the cancel endpoint is a read-then-write path — its write
    must run as BEGIN IMMEDIATE. Falsified both ways:

    * guard ACTIVE: a concurrent writer committing while the request is in
      flight cannot kill the cancel (200, job cancelled);
    * guard NEUTERED (deferred — the old behavior): the SAME interleaving
      dies on the unretryable BUSY_SNAPSHOT (500).

    The concurrent commit is injected between the job READ and the UPDATE via
    a spy around ``request_cancellation``.
    """
    import sqlite3
    from contextlib import asynccontextmanager

    import evoblue_video_mcp.web.app as web_app_module
    from evoblue_video_mcp.storage.repository import _get

    original = web_app_module.request_cancellation

    async with session_factory() as sess:
        await _enqueue(sess, job_id="cancel-race")

    async def spy_read_then_concurrent_commit(session, **kwargs):
        # The READ opens the (would-be deferred) snapshot...
        await _get(session, job_id=kwargs["job_id"])
        # ...while it is open, ANOTHER writer commits. A deferred read never
        # blocks this writer; under IMMEDIATE the writer waits for OUR lock
        # instead, bounded by its own (short) timeout — and loses.
        conn = sqlite3.connect(str(tmp_path / "test.db"), timeout=0.5)
        try:
            conn.execute(
                "UPDATE index_status SET started_at = COALESCE(started_at, 0) + 1 "
                "WHERE id = 1"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass  # guard active: the concurrent writer waits and loses
        finally:
            conn.close()
        return await original(session, **kwargs)

    @asynccontextmanager
    async def no_guard(session):  # neutered guard = the old deferred behavior
        yield

    async def run_cancel(job_id: str) -> httpx.Response:
        app = create_app(session_factory=session_factory)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            return await client.post(f"/api/jobs/{job_id}/cancel")

    # 1. Guard ACTIVE: the interleaving survives.
    monkeypatch.setattr(web_app_module, "request_cancellation", spy_read_then_concurrent_commit)
    resp = await run_cancel("cancel-race")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == JobStatus.CANCELLED.value

    # 2. Guard NEUTERED: the SAME interleaving kills the request. Both layers
    #    must be neutered — the endpoint guard AND the repository writer's own
    #    _begin_immediate (the function is self-governing by design).
    import evoblue_video_mcp.storage.repository as repository_module

    async def _no_immediate(session):
        pass

    async with session_factory() as sess:
        await _enqueue(sess, job_id="cancel-race-2")
    monkeypatch.setattr(web_app_module, "immediate_write_transaction", no_guard)
    monkeypatch.setattr(repository_module, "_begin_immediate", _no_immediate)
    # Starlette's ServerErrorMiddleware re-raises after the 500, so under the
    # raw ASGI transport the OperationalError surfaces to the caller.
    import sqlalchemy.exc

    with pytest.raises(sqlalchemy.exc.OperationalError):
        await run_cancel("cancel-race-2")
    monkeypatch.undo()


async def test_setup_completed_requires_runnable_llm_configuration(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """P1 review round 2: the backend, not just the UI, enforces the worker gate."""
    app = create_app(session_factory=session_factory, credential_store=_MemoryCredentials())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1) bare completion on a fresh install -> rejected, nothing persisted
        r = await client.put("/api/settings", json={"setup_completed": True})
        assert r.status_code == 400
        assert "不可运行" in r.json()["detail"]
        state = (await client.get("/api/settings")).json()
        assert state["setup_completed"] is False

        # 2) provider/base/model without any key -> still not runnable
        r = await client.put(
            "/api/settings",
            json={
                "setup_completed": True,
                "llm_provider": "openai",
                "llm_base_url": "https://api.openai.com/v1",
                "llm_model": "gpt-5",
            },
        )
        assert r.status_code == 400
        state = (await client.get("/api/settings")).json()
        assert state["setup_completed"] is False

        # 3) with the key the same PUT completes
        r = await client.put(
            "/api/settings",
            json={
                "setup_completed": True,
                "llm_provider": "openai",
                "llm_base_url": "https://api.openai.com/v1",
                "llm_model": "gpt-5",
                "llm_api_key": "sk-runnable",
            },
        )
        assert r.status_code == 200
        assert r.json()["setup_completed"] is True
        assert r.json()["llm_api_key_configured"] is True


async def test_provider_switch_requires_new_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Switching provider points the credential ref at an empty slot: the
    runnable invariant must hold against the FINAL ref, not the old key."""
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = {
            "llm_provider": "deepseek",
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-chat",
            "llm_api_key": "sk-deepseek",
            "setup_completed": True,
        }
        assert (await client.put("/api/settings", json=base)).status_code == 200

        # switch provider, keep the old key by omitting llm_api_key -> reject
        switched = dict(base, llm_provider="openai")
        switched.pop("llm_api_key")
        switched["llm_base_url"] = "https://api.openai.com/v1"
        switched["llm_model"] = "gpt-5"
        r = await client.put("/api/settings", json=switched)
        assert r.status_code == 400
        state = (await client.get("/api/settings")).json()
        assert state["llm_provider"] == "deepseek", "rejected save must not persist"

        # same switch WITH the new key -> accepted
        switched["llm_api_key"] = "sk-openai"
        r = await client.put("/api/settings", json=switched)
        assert r.status_code == 200
        state = (await client.get("/api/settings")).json()
        assert state["llm_provider"] == "openai"
        assert state["llm_api_key_configured"] is True


async def test_empty_key_payload_counts_as_deleted_for_invariant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An explicit empty llm_api_key deletes the stored secret: completing
    setup in the same request must be rejected even though the OLD key still
    sits in the credential store."""
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = {
            "llm_provider": "deepseek",
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-chat",
            "llm_api_key": "sk-deepseek",
            "setup_completed": True,
        }
        assert (await client.put("/api/settings", json=base)).status_code == 200

        r = await client.put(
            "/api/settings",
            json={"llm_api_key": "", "setup_completed": True},
        )
        assert r.status_code == 400
        state = (await client.get("/api/settings")).json()
        assert state["setup_completed"] is True, "earlier completion survives"


async def test_same_provider_with_stored_key_allows_bare_completion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The legitimate leave-key-empty path: same provider, key in the store."""
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = {
            "llm_provider": "deepseek",
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-chat",
            "llm_api_key": "sk-deepseek",
            "setup_completed": True,
        }
        assert (await client.put("/api/settings", json=base)).status_code == 200

        r = await client.put("/api/settings", json={"setup_completed": True})
        assert r.status_code == 200
        assert r.json()["llm_api_key_configured"] is True


async def test_partial_update_on_completed_record_cannot_break_runnability(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """P1 review round 3: the gate watches the FINAL state, not the payload.

    An already-completed record must not be degradable through a partial
    update that omits setup_completed — deleting the key or switching the
    provider would otherwise persist while the flag stays true and the worker
    stays idle.
    """
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = {
            "llm_provider": "deepseek",
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-chat",
            "llm_api_key": "sk-deepseek",
            "setup_completed": True,
        }
        assert (await client.put("/api/settings", json=base)).status_code == 200

        # 1) delete the key WITHOUT carrying setup_completed -> rejected,
        #    database and keyring both unchanged
        r = await client.put("/api/settings", json={"llm_api_key": ""})
        assert r.status_code == 400
        state = (await client.get("/api/settings")).json()
        assert state["setup_completed"] is True
        assert state["llm_api_key_configured"] is True
        assert credentials.values == {"llm:deepseek": "sk-deepseek"}

        # 2) switch provider WITHOUT a new key -> rejected, unchanged
        r = await client.put(
            "/api/settings",
            json={
                "llm_provider": "openai",
                "llm_base_url": "https://api.openai.com/v1",
                "llm_model": "gpt-5",
            },
        )
        assert r.status_code == 400
        state = (await client.get("/api/settings")).json()
        assert state["llm_provider"] == "deepseek"
        assert credentials.values == {"llm:deepseek": "sk-deepseek"}

        # 3) partial updates that keep runnability stay allowed
        r = await client.put(
            "/api/settings", json={"report_directory": "C:/reports"}
        )
        assert r.status_code == 200

        # 4) explicit downgrade is the sanctioned way out: allowed, keyring
        #    and database reflect the degraded state
        r = await client.put(
            "/api/settings", json={"llm_api_key": "", "setup_completed": False}
        )
        assert r.status_code == 200
        state = (await client.get("/api/settings")).json()
        assert state["setup_completed"] is False
        assert state["llm_api_key_configured"] is False
        assert credentials.values == {}
