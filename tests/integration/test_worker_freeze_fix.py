"""Regression tests for the silent worker-freeze cascade (2026-10-31).

A checkpoint write on an already-registered artifact used to return while
holding an open IMMEDIATE transaction, so the stage session kept the database
write lock for the rest of a long transcription; the lease heartbeat then died
on ``database is locked``, and ``await heartbeat`` re-raised that raw error
through every guard, silently killing the worker loop and freezing the job in
a running state. Each test below pins one layer of the fix.
"""

import asyncio
import logging

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.runtime import bootstrap
from evoblue_video_mcp.runtime.worker import (
    StageOutcome,
    _heartbeat_lease,
    run_worker_once,
)
from evoblue_video_mcp.storage.models import Job, JobArtifact
from evoblue_video_mcp.storage.repository import (
    enqueue_job,
    get_job,
    renew_lease,
    save_artifact_inline,
)


class _ForwardHandler:
    def __init__(self, target: JobStatus) -> None:
        self._target = target

    async def execute(self, job: Job, session: AsyncSession, ctx) -> StageOutcome:
        return StageOutcome.success(self._target)


async def _enqueue_claimed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: str = "job-1",
    status: JobStatus = JobStatus.SUMMARIZING_CHUNKS,
    attempt: int = 1,
    max_attempts: int = 3,
    **extra,
) -> None:
    async with session_factory() as sess:
        await enqueue_job(
            sess,
            job_id=job_id,
            url="https://www.youtube.com/watch?v=abc",
            request_fingerprint=f"key-{job_id}",
            config_fingerprint="fp-1",
            now=1000.0,
            status=status,
            attempt=attempt,
            max_attempts=max_attempts,
            **extra,
        )


async def _save_checkpoint(sess: AsyncSession, payload: str) -> None:
    await save_artifact_inline(
        sess,
        job_id="job-1",
        owner="worker-a",
        now=1000.0,
        stage="summarizing_chunks",
        artifact_type="chunk_summary",
        input_fingerprint="fp",
        schema_version=1,
        payload_json=payload,
    )


async def test_checkpoint_refresh_releases_write_lock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Refreshing an existing checkpoint must not strand an open IMMEDIATE
    transaction: another connection has to be able to take the write lock
    immediately afterwards, or the lease heartbeat starves (root cause of the
    freeze)."""
    await _enqueue_claimed(session_factory)
    async with session_factory() as sess:
        from evoblue_video_mcp.storage.repository import claim_job

        await claim_job(sess, job_id="job-1", owner="worker-a", lease_seconds=30.0, now=1000.0)

        await _save_checkpoint(sess, "seg-1")
        assert not sess.in_transaction(), "insert path must commit"

        # The path that used to leak: artifact row already exists.
        await _save_checkpoint(sess, "seg-1+seg-2")
        assert not sess.in_transaction(), "refresh path must commit"

    async with session_factory() as sess:
        artifacts = (
            await sess.scalars(select(JobArtifact).where(JobArtifact.job_id == "job-1"))
        ).all()
        assert len(artifacts) == 1
        assert artifacts[0].payload_json == "seg-1+seg-2", "checkpoint must grow"

    # The heartbeat's BEGIN IMMEDIATE must win the write lock instantly.
    async with session_factory() as sess:
        assert await renew_lease(
            sess, job_id="job-1", owner="worker-a", now=1010.0, lease_seconds=30.0
        )


async def test_heartbeat_survives_transient_lock(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renewal that loses a write-lock race must retry on the next interval
    instead of killing the heartbeat task."""
    import evoblue_video_mcp.runtime.worker as worker_mod

    calls = {"n": 0}
    real_renew = worker_mod.renew_lease

    async def flaky_renew(session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked"))
        return await real_renew(session, **kwargs)

    monkeypatch.setattr(worker_mod, "renew_lease", flaky_renew)

    await _enqueue_claimed(session_factory)
    async with session_factory() as sess:
        from evoblue_video_mcp.storage.repository import claim_job

        await claim_job(sess, job_id="job-1", owner="worker-a", lease_seconds=30.0, now=1000.0)

    stop = asyncio.Event()
    hb = asyncio.create_task(
        _heartbeat_lease(
            session_factory,
            job_id="job-1",
            owner="worker-a",
            lease_seconds=0.6,
            now=lambda: 1005.0,
            stop=stop,
        )
    )
    deadline = asyncio.get_running_loop().time() + 5.0
    while calls["n"] < 2 and not hb.done():
        assert asyncio.get_running_loop().time() < deadline, "heartbeat never retried"
        await asyncio.sleep(0.01)
    stop.set()
    # Old behavior: the task already died with OperationalError before the
    # second attempt; awaiting it re-raises.
    await asyncio.wait_for(hb, timeout=5.0)


async def test_reap_heartbeat_contains_unexpected_death(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A heartbeat that died unexpectedly must not take the worker's outcome
    down with it; a genuine lease loss still propagates."""
    import evoblue_video_mcp.runtime.worker as worker_mod

    reap = getattr(worker_mod, "_reap_heartbeat", None)
    assert reap is not None, "containment helper must exist"

    boom = asyncio.get_running_loop().create_task(_raise(RuntimeError("boom")))
    await reap(boom)  # must not raise

    lost = asyncio.get_running_loop().create_task(_raise_late())
    with pytest.raises(Exception, match="lease lost"):
        await reap(lost)


async def _raise(exc: BaseException) -> None:
    raise exc


async def _raise_late() -> None:
    from evoblue_video_mcp.storage.repository import LeaseLostError

    raise LeaseLostError("lease lost for job 'x'")


async def test_worker_survives_dead_heartbeat_task(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Even if the heartbeat task dies outright mid-stage, the worker must
    finish the job instead of the heartbeat's exception escaping through
    ``await heartbeat`` and freezing the job in a running state."""
    import evoblue_video_mcp.runtime.worker as worker_mod

    async def dying_heartbeat(*args, **kwargs):
        raise OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked"))

    monkeypatch.setattr(worker_mod, "_heartbeat_lease", dying_heartbeat)

    await _enqueue_claimed(session_factory, status=JobStatus.FETCHING_METADATA)
    handlers = {
        src: _ForwardHandler(dst)
        for src, dst in [
            (JobStatus.FETCHING_METADATA, JobStatus.FETCHING_SUBTITLES),
            (JobStatus.FETCHING_SUBTITLES, JobStatus.DOWNLOADING_AUDIO),
            (JobStatus.DOWNLOADING_AUDIO, JobStatus.TRANSCRIBING),
            (JobStatus.TRANSCRIBING, JobStatus.CLEANING_TRANSCRIPT),
            (JobStatus.CLEANING_TRANSCRIPT, JobStatus.CHUNKING),
            (JobStatus.CHUNKING, JobStatus.SUMMARIZING_CHUNKS),
            (JobStatus.SUMMARIZING_CHUNKS, JobStatus.GENERATING_REPORT),
            (JobStatus.GENERATING_REPORT, JobStatus.INDEXING),
            (JobStatus.INDEXING, JobStatus.COMPLETED),
        ]
    }
    with caplog.at_level(logging.ERROR, logger="evoblue_video_mcp.runtime.worker"):
        job = await run_worker_once(
            session_factory,
            owner="worker-a",
            lease_seconds=30.0,
            now_fn=lambda: 1000.0,
            handlers=handlers,
        )
    assert job is not None
    assert job.status == JobStatus.COMPLETED.value
    assert any("heartbeat" in r.getMessage().lower() for r in caplog.records)


async def test_loop_survives_poisoned_iteration(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected crash inside run_worker_once must not kill the runtime
    worker loop: it logs and keeps serving instead of stranding every queued
    job behind a silently dead task."""
    import evoblue_video_mcp.runtime.bootstrap as bootstrap_mod

    calls = {"n": 0}

    async def poisoned_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked"))
        return None

    monkeypatch.setattr(bootstrap_mod, "run_worker_once", poisoned_once)

    async def factory(settings):
        return {}

    stop = asyncio.Event()
    loop = asyncio.create_task(
        bootstrap.run_runtime_worker_loop(
            session_factory,
            owner="worker-a",
            lease_seconds=30.0,
            idle_sleep=0.01,
            stop=stop,
            handler_factory=factory,
        )
    )
    deadline = asyncio.get_running_loop().time() + 5.0
    while calls["n"] < 2 and not loop.done():
        assert asyncio.get_running_loop().time() < deadline, "loop never continued"
        await asyncio.sleep(0.01)
    stop.set()
    # Old behavior: the loop task itself raised on the first iteration and
    # awaiting it re-raises the poisoned exception.
    await asyncio.wait_for(loop, timeout=5.0)


async def test_exhausted_running_job_is_failed_on_recovery(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A running-state job whose attempts are exhausted can never be claimed
    again; startup recovery must land it in ``failed`` rather than leave an
    unclaimable zombie frozen mid-stage."""
    from evoblue_video_mcp.storage.repository import fail_exhausted_retries

    await _enqueue_claimed(
        session_factory, job_id="zombie", status=JobStatus.TRANSCRIBING, attempt=3, max_attempts=3
    )
    await _enqueue_claimed(
        session_factory,
        job_id="healthy-running",
        status=JobStatus.TRANSCRIBING,
        attempt=1,
        max_attempts=3,
    )
    await _enqueue_claimed(
        session_factory,
        job_id="exhausted-waiting",
        status=JobStatus.RETRY_WAIT,
        attempt=2,
        max_attempts=2,
        next_retry_at=900.0,
    )

    async with session_factory() as sess:
        failed = await fail_exhausted_retries(sess, now=1000.0)
    assert {j.job_id for j in failed} == {"zombie", "exhausted-waiting"}

    async with session_factory() as sess:
        zombie = await get_job(sess, job_id="zombie")
        assert zombie is not None
        assert zombie.status == JobStatus.FAILED.value
        assert zombie.error_code == "MAX_ATTEMPTS_EXCEEDED"
        assert zombie.lease_owner is None

        healthy = await get_job(sess, job_id="healthy-running")
        assert healthy is not None
        assert healthy.status == JobStatus.TRANSCRIBING.value
