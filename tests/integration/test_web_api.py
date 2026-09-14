"""Local Engine HTTP API: jobs observation and first-setup settings."""

import time

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.config import Settings
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.storage.repository import enqueue_job, get_app_settings
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
    session: AsyncSession,
    *,
    job_id: str,
    status: JobStatus = JobStatus.QUEUED,
    now: float = 1000.0,
) -> None:
    await enqueue_job(
        session,
        job_id=job_id,
        url="https://www.youtube.com/watch?v=abc",
        request_fingerprint=f"key-{job_id}",
        config_fingerprint="fp-1",
        now=now,
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

    # R5: the key lives in ONE per-write versioned slot (bare provider-name
    # slots are no longer written), and never in the database.
    assert list(credentials.values.values()) == ["super-secret-key"]
    assert all(ref.startswith("llm:openai:") for ref in credentials.values)


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
        assert list(credentials.values.values()) == ["sk-deepseek"]

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
        assert list(credentials.values.values()) == ["sk-deepseek"]

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


async def test_settings_put_origin_change_without_key_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R1 (review): the save path applies the same credential boundary — an
    absent key may keep the stored secret only when the target ORIGIN is
    unchanged; editing the Base URL to another site requires an explicit key
    (otherwise the worker would ship the old key to the new endpoint)."""
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with _client(session_factory) as _unused, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = {
            "llm_provider": "deepseek",
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-flash",
            "llm_api_key": "sk-deepseek",
            "setup_completed": True,
        }
        assert (await client.put("/api/settings", json=base)).status_code == 200

        r = await client.put(
            "/api/settings",
            json={
                "llm_provider": "deepseek",
                "llm_base_url": "https://api.deepseek.com:8443",
                "llm_model": "deepseek-flash",
            },
        )
        assert r.status_code == 400
        state = (await client.get("/api/settings")).json()
        assert state["llm_base_url"] == "https://api.deepseek.com"
        assert state["llm_api_key_configured"] is True


async def test_settings_put_origin_change_with_key_allowed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R1: an explicit key for the new endpoint saves fine."""
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = {
            "llm_provider": "deepseek",
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-flash",
            "llm_api_key": "sk-old",
            "setup_completed": True,
        }
        assert (await client.put("/api/settings", json=base)).status_code == 200

        r = await client.put(
            "/api/settings",
            json={
                "llm_provider": "deepseek",
                "llm_base_url": "https://gateway.example/v1",
                "llm_model": "deepseek-flash",
                "llm_api_key": "sk-new-gateway",
                "setup_completed": True,
            },
        )
        assert r.status_code == 200
        state = (await client.get("/api/settings")).json()
        assert state["llm_base_url"] == "https://gateway.example/v1"
        assert await _drain(
            lambda: list(credentials.values.values()) == ["sk-new-gateway"]
        ), "the replaced slot is cleaned up after the commit (R5)"
        assert all(ref.startswith("llm:deepseek:") for ref in credentials.values)


async def test_late_credential_write_cannot_overwrite_newer_save(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2/R5 (review rounds 2-3): a timed-out credential write keeps its
    place on the serial writer and lands LATE — but into its own fresh slot
    that nothing references, followed by its cleanup. Nothing the database
    still points at can be overwritten, and the vault converges to empty
    when no save ever committed."""
    import threading

    import evoblue_video_mcp.web.app as web_app

    class _FirstWriteBlocks:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.release_first = threading.Event()
            self.first_done = threading.Event()
            self.calls = 0

        def get_secret(self, reference: str) -> str | None:
            return self.values.get(reference)

        def set_secret(self, reference: str, secret: str) -> None:
            self.calls += 1
            if self.calls == 1:
                assert self.release_first.wait(timeout=30)
            self.values[reference] = secret
            if self.calls == 1:
                self.first_done.set()

        def delete_secret(self, reference: str) -> None:
            self.values.pop(reference, None)

    monkeypatch.setattr(web_app, "_KEYRING_TIMEOUT_S", 0.05)
    store = _FirstWriteBlocks()
    app = create_app(session_factory=session_factory, credential_store=store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = {
            "llm_provider": "deepseek",
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-flash",
            "llm_api_key": "sk-first",
            "setup_completed": True,
        }
        first = await client.put("/api/settings", json=base)
        assert first.status_code == 503, "blocked first write answers not-confirmed"

        second = await client.put("/api/settings", json={**base, "llm_api_key": "sk-second"})
        # Queued behind the blocked write: also not-confirmed (or 200 only if
        # the first finished in time — either way ordering is preserved).
        assert second.status_code in (200, 503)

        store.release_first.set()
        assert await _drain(store.first_done.is_set)
        # Both writes landed late into slots nothing references, and each
        # cleanup ran behind its write: the vault drains to empty and no
        # database row ever points at a foreign key.
        assert await _drain(lambda: store.values == {}), (
            "late unadopted writes must be cleaned up, never left behind (R5)"
        )
        row = await _settings_row(session_factory)
        assert row is None or row.llm_credential_ref is None


async def test_late_credential_delete_cannot_remove_newer_save(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2/R5: deletion now unbinds the DATABASE first; the slot cleanup is
    queued behind any still-running write. A blocked late write can never
    resurrect or destroy a credential the committed state depends on: the
    final state is exactly "unbound, vault empty"."""
    import threading

    import evoblue_video_mcp.web.app as web_app

    class _SecondWriteBlocks:
        """First set passes (seeds the persisted settings row so the delete
        has provider context); the SECOND set blocks — the timed-out write
        under test."""

        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.release_first = threading.Event()
            self.first_done = threading.Event()
            self.calls = 0

        def get_secret(self, reference: str) -> str | None:
            return self.values.get(reference)

        def set_secret(self, reference: str, secret: str) -> None:
            self.calls += 1
            if self.calls == 2:
                assert self.release_first.wait(timeout=30)
            self.values[reference] = secret
            if self.calls == 2:
                self.first_done.set()

        def delete_secret(self, reference: str) -> None:
            self.values.pop(reference, None)

    monkeypatch.setattr(web_app, "_KEYRING_TIMEOUT_S", 0.05)
    store = _SecondWriteBlocks()
    app = create_app(session_factory=session_factory, credential_store=store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = {
            "llm_provider": "deepseek",
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-flash",
            "llm_api_key": "sk-seed",
            "setup_completed": True,
        }
        seeded = await client.put("/api/settings", json=base)
        assert seeded.status_code == 200, "seed save persists the settings row"

        first = await client.put("/api/settings", json={**base, "llm_api_key": "sk-stale"})
        assert first.status_code == 503

        second = await client.put(
            "/api/settings", json={"llm_api_key": "", "setup_completed": False}
        )
        assert second.status_code == 200, "the delete unbinds the database directly"

        store.release_first.set()
        assert await _drain(store.first_done.is_set)
        # the late stale write landed and was cleaned; the seeded slot was
        # deleted only after the database had adopted ref=None
        assert await _drain(lambda: store.values == {}), (
            "the late old write resurrected a deleted credential (R2 counterexample)"
        )
        row = await _settings_row(session_factory)
        assert row is not None and row.llm_credential_ref is None


async def test_jobs_pagination_covers_all_rows_with_server_side_filters(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R4 (review): server-side paging and filtering must come from one
    collection — 205 failed rows page through completely with no gap or
    duplicate, and platform/query filter server-side (the bridge pushes
    them down instead of slicing a locally filtered window)."""
    from sqlalchemy import update

    from evoblue_video_mcp.storage.models import Job

    async with session_factory() as sess:
        for i in range(205):
            await _enqueue(sess, job_id=f"f{i:03d}", status=JobStatus.FAILED)
        await sess.execute(
            update(Job)
            .where(Job.job_id.in_(["f000", "f001", "f002"]))
            .values(
                platform="bilibili",
                title="B 站样本",
                url="https://www.bilibili.com/video/BVsample",
            )
        )
        await sess.commit()

    app = create_app(session_factory=session_factory, local_token=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        seen: list[str] = []
        offset = 0
        while True:
            resp = await client.get(
                "/api/jobs", params={"status": "failed", "limit": 100, "offset": offset}
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 205
            ids = [item["job_id"] for item in body["items"]]
            seen.extend(ids)
            offset += 100
            if offset >= body["total"]:
                break
        assert len(seen) == 205
        assert len(set(seen)) == 205, "no duplicates across pages"
        assert seen == sorted(seen, reverse=True) or len(set(seen)) == 205

        # page 6 exactly (the review's offset=100 window)
        resp = await client.get(
            "/api/jobs", params={"status": "failed", "limit": 20, "offset": 100}
        )
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 20
        assert resp.json()["total"] == 205

        # server-side platform filter: exact set, exact total
        resp = await client.get("/api/jobs", params={"platform": "BILIBILI"})
        body = resp.json()
        assert {item["job_id"] for item in body["items"]} == {"f000", "f001", "f002"}
        assert body["total"] == 3

        # server-side query filter over title (and url fallback semantics)
        resp = await client.get("/api/jobs", params={"query": "B 站样本"})
        assert {item["job_id"] for item in resp.json()["items"]} == {"f000", "f001", "f002"}
        assert resp.json()["total"] == 3
        resp = await client.get("/api/jobs", params={"query": "youtube.com"})
        assert resp.json()["total"] == 202  # url fallback matches the rest


async def test_setup_false_cannot_bypass_credential_origin_boundary(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1b (review round 2): the credential-origin boundary must hold
    independent of setup_completed. Sequence 1 — close setup and change the
    Base URL in ONE request: the old key must not later ship to the new site
    via settings/test or a setup re-enable."""
    import evoblue_video_mcp.web.app as web_app

    seen: dict[str, object] = {}

    async def probe(url: str, headers) -> int:
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        return 200

    monkeypatch.setattr(web_app, "_llm_status_probe", lambda: probe)
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = {
            "llm_provider": "deepseek",
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-flash",
            "llm_api_key": "sk-original",
            "setup_completed": True,
        }
        assert (await client.put("/api/settings", json=base)).status_code == 200

        # step 1: close setup AND change origin in one request, no key
        r = await client.put(
            "/api/settings",
            json={"setup_completed": False, "llm_base_url": "https://different.example"},
        )
        assert r.status_code == 200, "the change itself is allowed..."
        state = (await client.get("/api/settings")).json()
        assert state["llm_api_key_configured"] is False, (
            "...but the OLD credential binding must be dropped atomically"
        )

        # step 2: settings/test with no key must NOT send the old key anywhere
        r = await client.post("/api/settings/test", json={})
        assert r.status_code == 200
        assert r.json()["status"] == "not_configured"
        assert "url" not in seen, "old key must never be sent to the new site"

        # step 3: re-enabling setup without a key is rejected by the gate
        r = await client.put("/api/settings", json={"setup_completed": True})
        assert r.status_code == 400


async def test_close_then_change_then_reenable_sequence(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1b: sequence 2/3 — close setup first, THEN edit the URL, then re-open.
    Each transition must preserve the boundary."""
    import evoblue_video_mcp.web.app as web_app

    seen: dict[str, object] = {}

    async def probe(url: str, headers) -> int:
        seen["url"] = url
        return 200

    monkeypatch.setattr(web_app, "_llm_status_probe", lambda: probe)
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = {
            "llm_provider": "deepseek",
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-flash",
            "llm_api_key": "sk-original",
            "setup_completed": True,
        }
        assert (await client.put("/api/settings", json=base)).status_code == 200

        assert (
            await client.put("/api/settings", json={"setup_completed": False})
        ).status_code == 200
        # editing the origin while setup is closed still drops the binding
        r = await client.put(
            "/api/settings", json={"llm_base_url": "https://different.example"}
        )
        assert r.status_code == 200
        state = (await client.get("/api/settings")).json()
        assert state["llm_api_key_configured"] is False

        # re-open without a new key: rejected; the old secret never leaks
        r = await client.put("/api/settings", json={"setup_completed": True})
        assert r.status_code == 400
        r = await client.post("/api/settings/test", json={})
        assert r.json()["status"] == "not_configured"
        assert "url" not in seen
        # the old secret itself remains in the store, merely unbound
        assert list(credentials.values.values()) == ["sk-original"]


async def test_jobs_status_group_and_history_query_server_side(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R4b: the default view's jobs segment (status_group=non_completed) and
    the history endpoint's query filter both run server-side — exact totals
    from one filtered collection."""
    from sqlalchemy import update

    from evoblue_video_mcp.storage.models import Job

    app = create_app(session_factory=session_factory, local_token=None)
    async with session_factory() as sess:
        await _enqueue(sess, job_id="g1", status=JobStatus.QUEUED)
        await _enqueue(sess, job_id="g2", status=JobStatus.FAILED)
        await _enqueue(sess, job_id="g3", status=JobStatus.CANCELLED)
        await _enqueue(sess, job_id="g4", status=JobStatus.COMPLETED)
        await sess.execute(
            update(Job).where(Job.job_id == "g2").values(platform="bilibili")
        )
        await sess.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/jobs", params={"status_group": "non_completed"})
        body = resp.json()
        ids = {i["job_id"] for i in body["items"]}
        assert ids == {"g1", "g2", "g3"}, "completed excluded, terminal kept"
        assert body["total"] == 3

        resp = await client.get(
            "/api/jobs",
            params={"status_group": "non_completed", "platform": "bilibili"},
        )
        assert {i["job_id"] for i in resp.json()["items"]} == {"g2"}
        assert resp.json()["total"] == 1

        # unknown groups are rejected, not silently ignored
        resp = await client.get("/api/jobs", params={"status_group": "everything"})
        assert resp.status_code == 422

        # the history endpoint's query filter (title/source_url substring)
        import hashlib

        from evoblue_video_mcp.storage.report_repository import (
            FtsBodyTexts,
            ReportIndexEntry,
            upsert_report_document,
        )

        content = "# r1 选品方法论"
        data = content.encode("utf-8")
        entry = ReportIndexEntry(
            job_id="r1",
            analysis_id="r1",
            title="选品方法论",
            platform="youtube",
            author="a",
            video_id="r1",
            source_url="https://youtu.be/r1",
            published_at=None,
            analyzed_at=1.0,
            summary_mode="auto",
            tags=[],
            summary_preview=content[:200],
            relative_path="r1.md",
            content_hash=hashlib.sha256(data).hexdigest(),
            byte_size=len(data),
            doc_source="pipeline",
        )
        async with session_factory() as sess:
            await upsert_report_document(
                sess, entry=entry, body=FtsBodyTexts(summary=content, transcript=""), now=1.0
            )
            await sess.commit()
        resp = await client.get("/api/history", params={"query": "选品"})
        assert resp.status_code == 200
        assert [i["job_id"] for i in resp.json()["items"]] == ["r1"]
        assert resp.json()["total"] == 1
        resp = await client.get("/api/history", params={"query": "youtu.be/r1"})
        assert resp.json()["total"] == 1
        resp = await client.get("/api/history", params={"query": "不存在词"})
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Review round 3 (R5/R6/R7/R8) counterexamples. Every test asserts through
# the DATABASE row (ref/origin read back from SQLite), not the PUT response.
# ---------------------------------------------------------------------------


async def _drain(predicate: object, timeout_s: float = 10.0) -> bool:
    """Await a fire-and-forget side effect (best-effort cleanup) with a bound."""
    import asyncio

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return bool(predicate())


class _BlockableCredentials(_MemoryCredentials):
    """Memory store whose ``block_on_call``-th set_secret blocks until released."""

    def __init__(self, block_on_call: int) -> None:
        super().__init__()
        import threading

        self.release = threading.Event()
        self.block_done = threading.Event()
        self.calls = 0
        self.block_on_call = block_on_call

    def set_secret(self, reference: str, secret: str) -> None:
        self.calls += 1
        if self.calls == self.block_on_call:
            assert self.release.wait(timeout=30)
            self.values[reference] = secret
            self.block_done.set()
            return
        self.values[reference] = secret


_BASE_DEEPSEEK = {
    "llm_provider": "deepseek",
    "llm_base_url": "https://api.deepseek.com",
    "llm_model": "deepseek-flash",
    "setup_completed": True,
}


async def _settings_row(session_factory: async_sessionmaker[AsyncSession]) -> object:
    async with session_factory() as sess:
        return await get_app_settings(sess)


async def test_keyring_write_succeeds_db_fails_keeps_old_binding(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R5 (review round 3, P1): the keyring slot the database row still
    references must never be overwritten in place. Keyring write succeeds,
    the database save then fails: reading the secret THROUGH the database's
    ref must still yield endpoint A's old key — B's key must never ship to A
    and A's credential must not be lost."""
    import evoblue_video_mcp.web.app as web_app

    orig_save = web_app.save_app_settings
    state = {"fail": False}

    async def _maybe_fail(sess: AsyncSession, **kwargs: object) -> object:
        if state["fail"]:
            raise RuntimeError("simulated database failure")
        return await orig_save(sess, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(web_app, "save_app_settings", _maybe_fail)
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        seeded = await client.put(
            "/api/settings", json={**_BASE_DEEPSEEK, "llm_api_key": "sk-old-A"}
        )
        assert seeded.status_code == 200
        row = await _settings_row(session_factory)
        assert credentials.get_secret(row.llm_credential_ref) == "sk-old-A"

        state["fail"] = True
        r = await client.put(
            "/api/settings",
            json={
                **_BASE_DEEPSEEK,
                "llm_base_url": "https://api.deepseek-b.example",
                "llm_api_key": "sk-new-B",
            },
        )
        assert r.status_code == 503, "database failure answers 503"

        row = await _settings_row(session_factory)
        assert row.llm_base_url == "https://api.deepseek.com"
        assert row.llm_credential_origin == "https://api.deepseek.com"
        assert credentials.get_secret(row.llm_credential_ref) == "sk-old-A", (
            "the database-referenced slot was overwritten in place: endpoint B's "
            "key would be sent to endpoint A (R5 counterexample)"
        )


async def test_timed_out_late_write_cannot_retarget_old_binding(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R5: a credential write that times out (503) keeps running and lands
    LATE — it must land in a slot nothing references (and be cleaned up
    afterwards); the database binding and its secret stay intact for the OLD
    endpoint even if the user never sends a second request."""
    import evoblue_video_mcp.web.app as web_app

    monkeypatch.setattr(web_app, "_KEYRING_TIMEOUT_S", 0.05)
    store = _BlockableCredentials(block_on_call=2)
    app = create_app(session_factory=session_factory, credential_store=store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        seeded = await client.put(
            "/api/settings", json={**_BASE_DEEPSEEK, "llm_api_key": "sk-old-A"}
        )
        assert seeded.status_code == 200
        adopted = (await _settings_row(session_factory)).llm_credential_ref

        r = await client.put(
            "/api/settings",
            json={
                **_BASE_DEEPSEEK,
                "llm_base_url": "https://api.deepseek-b.example",
                "llm_api_key": "sk-new-B",
            },
        )
        assert r.status_code == 503

        store.release.set()
        assert await _drain(store.block_done.is_set)
        assert await _drain(lambda: "sk-new-B" not in store.values.values()), (
            "the late write's slot must not linger after cleanup"
        )
        row = await _settings_row(session_factory)
        assert row.llm_credential_ref == adopted
        assert store.get_secret(row.llm_credential_ref) == "sk-old-A", (
            "the late timed-out write overwrote the slot the old binding "
            "still references (R5 counterexample)"
        )


async def test_delete_unbinds_database_before_slot_cleanup(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R5: an explicit key deletion unbinds the database FIRST; the slot
    cleanup runs only after the row has adopted ref=None. A failed save must
    keep the current binding AND its secret fully usable."""
    import evoblue_video_mcp.web.app as web_app

    orig_save = web_app.save_app_settings
    state = {"fail": False}

    async def _maybe_fail(sess: AsyncSession, **kwargs: object) -> object:
        if state["fail"]:
            raise RuntimeError("simulated database failure")
        return await orig_save(sess, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(web_app, "save_app_settings", _maybe_fail)
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        seeded = await client.put(
            "/api/settings", json={**_BASE_DEEPSEEK, "llm_api_key": "sk-old-A"}
        )
        assert seeded.status_code == 200
        adopted = (await _settings_row(session_factory)).llm_credential_ref

        state["fail"] = True
        r = await client.put("/api/settings", json={"llm_api_key": "", "setup_completed": False})
        assert r.status_code == 503
        row = await _settings_row(session_factory)
        assert row.llm_credential_ref == adopted
        assert credentials.get_secret(row.llm_credential_ref) == "sk-old-A", (
            "keyring deletion ran before the database unbind: the old "
            "configuration references a deleted slot (R5 counterexample)"
        )

        state["fail"] = False
        ok = await client.put("/api/settings", json={"llm_api_key": "", "setup_completed": False})
        assert ok.status_code == 200
        row = await _settings_row(session_factory)
        assert row.llm_credential_ref is None
        assert await _drain(lambda: adopted not in credentials.values), (
            "the unbound slot must be cleaned up after the commit"
        )


async def test_key_update_uses_fresh_slot_and_cleans_replaced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R5: every key write targets its own fresh (versioned) reference; the
    replaced slot is deleted after the database adopts the new one, so the
    vault converges to exactly the referenced slot."""
    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        seeded = await client.put(
            "/api/settings", json={**_BASE_DEEPSEEK, "llm_api_key": "sk-old"}
        )
        assert seeded.status_code == 200
        first_ref = (await _settings_row(session_factory)).llm_credential_ref
        assert first_ref.startswith("llm:deepseek:"), (
            "key writes must use versioned per-write references"
        )

        r = await client.put("/api/settings", json={**_BASE_DEEPSEEK, "llm_api_key": "sk-new"})
        assert r.status_code == 200
        row = await _settings_row(session_factory)
        assert row.llm_credential_ref != first_ref
        assert credentials.get_secret(row.llm_credential_ref) == "sk-new"
        assert await _drain(lambda: first_ref not in credentials.values)
        assert credentials.values == {row.llm_credential_ref: "sk-new"}


async def test_legacy_credential_ref_still_reads_and_migrates_on_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R5 backward compatibility: rows saved before versioning keep the bare
    ``llm:{provider}`` reference readable; the next key write migrates the
    binding to a versioned slot and cleans the legacy one."""
    from evoblue_video_mcp.storage.repository import save_app_settings

    credentials = _MemoryCredentials()
    credentials.values["llm:deepseek"] = "sk-legacy"
    async with session_factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            now=1000.0,
            llm_provider="deepseek",
            llm_base_url="https://api.deepseek.com",
            llm_model="deepseek-flash",
            llm_credential_ref="llm:deepseek",
            llm_credential_origin="https://api.deepseek.com",
        )
        await sess.commit()

    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        state = (await client.get("/api/settings")).json()
        assert state["llm_api_key_configured"] is True

        r = await client.put(
            "/api/settings", json={**_BASE_DEEPSEEK, "llm_api_key": "sk-fresh"}
        )
        assert r.status_code == 200
        row = await _settings_row(session_factory)
        assert row.llm_credential_ref.startswith("llm:deepseek:")
        assert credentials.get_secret(row.llm_credential_ref) == "sk-fresh"
        assert await _drain(lambda: "llm:deepseek" not in credentials.values)
        assert credentials.values == {row.llm_credential_ref: "sk-fresh"}


def _report_entry(job_id: str, *, platform: str = "youtube", analyzed_at: float = 100.0) -> object:
    import hashlib

    from evoblue_video_mcp.storage.report_repository import ReportIndexEntry

    content = f"# {job_id} content"
    data = content.encode("utf-8")
    return ReportIndexEntry(
        job_id=job_id,
        analysis_id=job_id,
        title=f"title-{job_id}",
        platform=platform,
        author="a",
        video_id=job_id,
        source_url=f"https://youtu.be/{job_id}",
        published_at=None,
        analyzed_at=analyzed_at,
        summary_mode="auto",
        tags=[],
        summary_preview=content[:200],
        relative_path=f"{job_id}.md",
        content_hash=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
        doc_source="pipeline",
    )


async def _index_report(
    session_factory: async_sessionmaker[AsyncSession], job_id: str, **kwargs: object
) -> None:
    from evoblue_video_mcp.storage.report_repository import FtsBodyTexts, upsert_report_document

    entry = _report_entry(job_id, **kwargs)  # type: ignore[arg-type]
    async with session_factory() as sess:
        await upsert_report_document(
            sess, entry=entry, body=FtsBodyTexts(summary=f"# {job_id}", transcript=""), now=1.0
        )
        await sess.commit()


async def test_history_excludes_unfinished_jobs_for_default_view(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R6 (review round 3, P1): a report whose job is NOT completed (the
    index commits inside IndexingHandler, the job advances in the worker's
    NEXT transaction — a failure in between leaves both stores populated)
    must be excludable so the default merge view pages two strictly
    disjoint segments: the job row wins, the report never duplicates it."""
    app = create_app(session_factory=session_factory, local_token=None)
    async with session_factory() as sess:
        await _enqueue(sess, job_id="run", status=JobStatus.TRANSCRIBING)
        await _enqueue(sess, job_id="jd", status=JobStatus.FAILED)
        await _enqueue(sess, job_id="jc", status=JobStatus.COMPLETED)
        await sess.commit()
    await _index_report(session_factory, "jd")
    await _index_report(session_factory, "jc")
    await _index_report(session_factory, "h1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        plain = await client.get("/api/history")
        assert plain.json()["total"] == 3
        assert {i["job_id"] for i in plain.json()["items"]} == {"jd", "jc", "h1"}

        disjoint = await client.get(
            "/api/history", params={"exclude_unfinished_jobs": "true"}
        )
        body = disjoint.json()
        assert body["total"] == 2
        assert {i["job_id"] for i in body["items"]} == {"jc", "h1"}, (
            "the non-completed job's report must be served by the jobs "
            "segment, never duplicated into the history segment (R6)"
        )


async def test_non_completed_orders_active_before_terminal(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R7 (review round 3, P2): the default view's contract order is
    运行中 → failed/cancelled → 报告历史. A NEWER failed job must not overtake
    an OLDER still-running one; equal timestamps fall back to the unique id
    for deterministic pagination."""
    app = create_app(session_factory=session_factory, local_token=None)
    async with session_factory() as sess:
        await _enqueue(sess, job_id="old-run", status=JobStatus.TRANSCRIBING, now=1000.0)
        await _enqueue(sess, job_id="new-fail", status=JobStatus.FAILED, now=2000.0)
        await _enqueue(sess, job_id="new-fail-2", status=JobStatus.CANCELLED, now=2000.0)
        await sess.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/jobs", params={"status_group": "non_completed"})
        ids = [i["job_id"] for i in resp.json()["items"]]
        assert ids == ["old-run", "new-fail-2", "new-fail"], (
            "active jobs must precede terminal ones regardless of age (R7)"
        )


async def test_history_platform_filter_matches_case_insensitively(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R8 (review round 3, P2): platform filtering is case-insensitive on
    BOTH data sources of the default/completed views — ``platform=YOUTUBE``
    must return the same rows as ``platform=youtube``."""
    app = create_app(session_factory=session_factory, local_token=None)
    await _index_report(session_factory, "h-yt", platform="youtube")
    await _index_report(session_factory, "h-bili", platform="bilibili")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        upper = await client.get("/api/history", params={"platform": "YOUTUBE"})
        assert upper.status_code == 200
        assert upper.json()["total"] == 1
        assert [i["job_id"] for i in upper.json()["items"]] == ["h-yt"]

        lower = await client.get("/api/history", params={"platform": "youtube"})
        assert lower.json()["total"] == 1
        assert [i["job_id"] for i in lower.json()["items"]] == ["h-yt"]


# ---------------------------------------------------------------------------
# Review round 4 (R9): the default merge view must come from ONE database
# snapshot — two separate requests can observe a worker commit in between.
# ---------------------------------------------------------------------------


async def test_unified_list_pages_both_segments_in_one_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R9: ``GET /api/jobs/unified`` is the default view's single-query source
    — active jobs first, then terminal job rows, then report history; a job
    whose report is indexed while its row is non-completed appears exactly
    once (as the job) and both segment totals are exact."""
    app = create_app(session_factory=session_factory, local_token=None)
    async with session_factory() as sess:
        await _enqueue(sess, job_id="run", status=JobStatus.TRANSCRIBING, now=1000.0)
        await _enqueue(sess, job_id="dup", status=JobStatus.FAILED, now=2000.0)
        await _enqueue(sess, job_id="dup2", status=JobStatus.INDEXING, now=3000.0)
        await _enqueue(sess, job_id="jc", status=JobStatus.COMPLETED, now=4000.0)
        await sess.commit()
    await _index_report(session_factory, "dup")
    await _index_report(session_factory, "dup2")
    await _index_report(session_factory, "jc", analyzed_at=200.0)
    await _index_report(session_factory, "h1", analyzed_at=100.0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/jobs/unified", params={"limit": 10})
        assert resp.status_code == 200
        body = resp.json()
        assert [(i["kind"], i["job_id"]) for i in body["items"]] == [
            ("job", "dup2"),    # active first, newest first (R7)
            ("job", "run"),
            ("job", "dup"),     # then terminal rows
            ("history", "jc"),  # then the report segment
            ("history", "h1"),
        ]
        assert body["jobs_total"] == 3
        assert body["history_total"] == 2
        assert body["total"] == 5, "one snapshot: total == jobs_total + history_total"

        # a page wholly inside the jobs segment still reports history_total
        page1 = await client.get("/api/jobs/unified", params={"limit": 2, "offset": 0})
        p1 = page1.json()
        assert [i["job_id"] for i in p1["items"]] == ["dup2", "run"]
        assert p1["total"] == 5 and p1["history_total"] == 2

        # a page wholly inside the history segment
        page3 = await client.get("/api/jobs/unified", params={"limit": 2, "offset": 3})
        p3 = page3.json()
        assert [i["job_id"] for i in p3["items"]] == ["jc", "h1"]
        assert p3["total"] == 5 and p3["jobs_total"] == 3


async def test_unified_list_snapshot_survives_completion_between_segments(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R9 (review round 4, P1): the worker may commit ``dup -> completed``
    BETWEEN the jobs read and the history read. With two independent
    requests the job then appears twice (job row + newly-eligible report)
    and the total double counts. The unified endpoint reads both segments
    inside ONE SQLite read transaction, so the whole answer — counts,
    exclusion, slices — reflects a single snapshot taken before the flip."""
    from sqlalchemy import update

    import evoblue_video_mcp.web.app as web_app
    from evoblue_video_mcp.storage.models import Job
    from evoblue_video_mcp.storage.report_repository import list_report_documents

    app = create_app(session_factory=session_factory, local_token=None)
    async with session_factory() as sess:
        await _enqueue(sess, job_id="dup", status=JobStatus.INDEXING, now=1000.0)
        await _enqueue(sess, job_id="jc", status=JobStatus.COMPLETED, now=2000.0)
        await sess.commit()
    await _index_report(session_factory, "dup")
    await _index_report(session_factory, "jc")

    original_list_reports = list_report_documents
    flipped = {"done": False}

    async def _flip_then_list(session, **kwargs):  # type: ignore[no-untyped-def]
        """Simulate the worker: commit dup->completed from ANOTHER connection
        right before the history read of THIS request runs."""
        if not flipped["done"] and kwargs.get("exclude_unfinished_jobs"):
            flipped["done"] = True
            async with session_factory() as other:
                await other.execute(
                    update(Job).where(Job.job_id == "dup").values(status="completed")
                )
                await other.commit()
        return await original_list_reports(session, **kwargs)

    monkeypatch.setattr(web_app, "list_report_documents", _flip_then_list)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/jobs/unified", params={"limit": 10})
        assert resp.status_code == 200
        body = resp.json()
        assert flipped["done"], "the counterexample injection must have run"
        ids = [i["job_id"] for i in body["items"]]
        assert ids.count("dup") == 1, (
            "the same job must not appear as both a job row and a history row "
            "when its completion commits between the two segment reads (R9)"
        )
        assert body["total"] == 2
        assert body["items"][0]["kind"] == "job" and body["items"][0]["job_id"] == "dup"



async def test_cancel_before_commit_cleans_unadopted_fresh_slot(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R12 (review round 5, P2): a cancellation that lands while the save is
    still inside the transaction body is PROVABLY uncommitted (the on_commit
    verdict fires on every durable path BEFORE the cancel propagates) — the
    fresh key slot must be cleaned up along with the pointer restore, not
    left as an invisible unreferenced key in the vault. Real task
    cancellation, blocked before the database body returns."""
    import asyncio

    import evoblue_video_mcp.web.app as web_app

    entered = asyncio.Event()

    async def _hanging_save(sess: AsyncSession, **kwargs: object) -> object:
        entered.set()
        await asyncio.Event().wait()  # never released — the cancel lands here
        raise AssertionError("unreachable")

    credentials = _MemoryCredentials()
    app = create_app(session_factory=session_factory, credential_store=credentials)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        seeded = await client.put(
            "/api/settings", json={**_BASE_DEEPSEEK, "llm_api_key": "sk-old"}
        )
        assert seeded.status_code == 200
        adopted = (await _settings_row(session_factory)).llm_credential_ref
        monkeypatch.setattr(web_app, "save_app_settings", _hanging_save)

        task = asyncio.create_task(
            client.put(
                "/api/settings", json={**_BASE_DEEPSEEK, "llm_api_key": "sk-new"}
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        row = await _settings_row(session_factory)
        assert row.llm_credential_ref == adopted
        assert credentials.get_secret(row.llm_credential_ref) == "sk-old"
        assert await _drain(lambda: "sk-new" not in credentials.values.values()), (
            "a pre-commit cancel provably left the write unadopted — the "
            "fresh slot must be cleaned up, not left in the vault (R12)"
        )
