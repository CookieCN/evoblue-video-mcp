"""Lease ownership: single winner, crash recovery, and terminal-state exclusion."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.jobs.transitions import TransitionError
from evoblue_video_mcp.storage.repository import (
    advance_job,
    claim_job,
    enqueue_job,
    recover_stale_jobs,
)


def _args(job_id: str = "job-1") -> dict:
    return {
        "job_id": job_id,
        "url": "https://www.youtube.com/watch?v=abc",
        "idempotency_key": f"key-{job_id}",
        "config_fingerprint": "fp-1",
    }


async def _enqueue(
    session: AsyncSession,
    *,
    status: JobStatus = JobStatus.QUEUED,
    now: float = 1000.0,
    **overrides,
) -> None:
    await enqueue_job(session, now=now, status=status, **_args(**overrides))


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
            await advance_job(sess, job_id="job-1", to_status=JobStatus.COMPLETED, now=1000.0)


async def test_legal_transition_persists(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)
        updated = await advance_job(
            sess, job_id="job-1", to_status=JobStatus.FETCHING_METADATA, now=1000.0
        )
        assert updated.status == JobStatus.FETCHING_METADATA.value
