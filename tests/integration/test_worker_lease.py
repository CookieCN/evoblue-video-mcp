"""Lease ownership: single winner, crash recovery, and terminal-state exclusion."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.jobs.transitions import TransitionError
from evoblue_video_mcp.storage import build_engine, init_db
from evoblue_video_mcp.storage.repository import (
    LeaseLostError,
    advance_job,
    claim_job,
    enqueue_job,
    fail_exhausted_retries,
    get_job,
    recover_stale_jobs,
)


async def _enqueue(
    session: AsyncSession,
    *,
    status: JobStatus = JobStatus.QUEUED,
    now: float = 1000.0,
    job_id: str = "job-1",
    max_attempts: int = 3,
    attempt: int = 0,
    next_retry_at: float | None = None,
) -> None:
    await enqueue_job(
        session,
        job_id=job_id,
        url="https://www.youtube.com/watch?v=abc",
        idempotency_key=f"key-{job_id}",
        config_fingerprint="fp-1",
        now=now,
        status=status,
        max_attempts=max_attempts,
        attempt=attempt,
        next_retry_at=next_retry_at,
    )


async def test_concurrent_claim_has_single_winner(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)

    async def worker(owner: str) -> object | None:
        async with session_factory() as sess:
            return await claim_job(
                sess, job_id="job-1", owner=owner, lease_seconds=30.0, now=1000.0
            )

    results = await asyncio.gather(worker("worker-a"), worker("worker-b"))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0].lease_owner in {"worker-a", "worker-b"}


async def test_expired_lease_is_reclaimable_after_crash(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)
        first = await claim_job(
            sess, job_id="job-1", owner="worker-a", lease_seconds=30.0, now=1000.0
        )
        assert first is not None
        assert first.lease_expires_at == 1030.0

    # Worker-a crashes without releasing. After the lease expires, recovery clears it.
    async with session_factory() as sess:
        recovered = await recover_stale_jobs(sess, now=1100.0)
        assert [j.job_id for j in recovered] == ["job-1"]

    async with session_factory() as sess:
        second = await claim_job(
            sess, job_id="job-1", owner="worker-b", lease_seconds=30.0, now=1100.0
        )
        assert second is not None
        assert second.lease_owner == "worker-b"


async def test_live_lease_is_not_reclaimable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)
        await claim_job(sess, job_id="job-1", owner="worker-a", lease_seconds=30.0, now=1000.0)

    async with session_factory() as sess:
        # Lease still valid at t=1010, so a second claim must fail.
        assert (
            await claim_job(sess, job_id="job-1", owner="worker-b", lease_seconds=30.0, now=1010.0)
        ) is None


async def test_recover_does_not_touch_live_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)
        await claim_job(sess, job_id="job-1", owner="worker-a", lease_seconds=30.0, now=1000.0)

    async with session_factory() as sess:
        # Lease valid until 1030; a recovery at 1010 must leave it untouched.
        assert await recover_stale_jobs(sess, now=1010.0) == []

    async with session_factory() as sess:
        job = await get_job(sess, job_id="job-1")
        assert job is not None
        assert job.lease_owner == "worker-a"


@pytest.mark.parametrize("terminal", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED])
async def test_terminal_status_is_never_claimed(
    session_factory: async_sessionmaker[AsyncSession],
    terminal: JobStatus,
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess, status=terminal)

    async with session_factory() as sess:
        assert (
            await claim_job(sess, job_id="job-1", owner="worker-a", lease_seconds=30.0, now=1000.0)
        ) is None


async def test_illegal_transition_is_rejected_at_repository_layer(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)
        with pytest.raises(TransitionError):
            await advance_job(
                sess,
                job_id="job-1",
                owner="worker-a",
                to_status=JobStatus.COMPLETED,
                now=1000.0,
                lease_seconds=30.0,
            )


async def test_legal_transition_persists(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)
        claimed = await claim_job(
            sess, job_id="job-1", owner="worker-a", lease_seconds=30.0, now=1000.0
        )
        assert claimed is not None
        updated = await advance_job(
            sess,
            job_id="job-1",
            owner="worker-a",
            to_status=JobStatus.FETCHING_SUBTITLES,
            now=1010.0,
            lease_seconds=30.0,
        )
        assert updated.status == JobStatus.FETCHING_SUBTITLES.value


async def test_stale_worker_cannot_advance_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)
        await claim_job(sess, job_id="job-1", owner="worker-a", lease_seconds=10.0, now=1000.0)

    # worker-a's lease expires at 1010. At 1020 worker-b takes over.
    async with session_factory() as sess:
        await recover_stale_jobs(sess, now=1020.0)
        taken = await claim_job(
            sess, job_id="job-1", owner="worker-b", lease_seconds=30.0, now=1020.0
        )
        assert taken is not None
        assert taken.lease_owner == "worker-b"

    # The stale worker-a no longer holds the lease, so advancing must be refused.
    async with session_factory() as sess:
        with pytest.raises(LeaseLostError):
            await advance_job(
                sess,
                job_id="job-1",
                owner="worker-a",
                to_status=JobStatus.FETCHING_SUBTITLES,
                now=1025.0,
                lease_seconds=30.0,
            )


async def test_attempt_limit_is_enforced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(
            sess, status=JobStatus.RETRY_WAIT, max_attempts=1, attempt=1, next_retry_at=900.0
        )

    async with session_factory() as sess:
        # attempt(1) already equals max_attempts(1), so it must not be claimable.
        assert (
            await claim_job(sess, job_id="job-1", owner="worker-a", lease_seconds=30.0, now=1000.0)
        ) is None
        exhausted = await fail_exhausted_retries(sess, now=1000.0)
        assert [j.job_id for j in exhausted] == ["job-1"]

    async with session_factory() as sess:
        job = await get_job(sess, job_id="job-1")
        assert job is not None
        assert job.status == JobStatus.FAILED.value
        assert job.error_code == "MAX_ATTEMPTS_EXCEEDED"
        assert job.retryable is False


async def test_repository_safe_with_default_session(tmp_path) -> None:
    """Repository must not depend on expire_on_commit=False set by test fixtures."""
    eng = build_engine(tmp_path / "default.db")
    await init_db(eng)
    factory = async_sessionmaker(eng)  # default expire_on_commit=True
    try:
        async with factory() as sess:
            job = await enqueue_job(
                sess,
                job_id="j1",
                url="https://www.youtube.com/watch?v=abc",
                idempotency_key="k1",
                config_fingerprint="fp",
                now=1000.0,
            )
            # Accessing fields after commit must not raise MissingGreenlet.
            assert job.job_id == "j1"
            assert job.status == JobStatus.QUEUED.value
    finally:
        await eng.dispose()
