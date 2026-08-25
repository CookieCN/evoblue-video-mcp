"""Engine startup recovery releases stale leases and fails exhausted retries."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.runtime.engine import recover_on_startup
from evoblue_video_mcp.storage.repository import claim_job, enqueue_job


async def _enqueue(
    session: AsyncSession,
    *,
    job_id: str,
    status: JobStatus = JobStatus.QUEUED,
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
        now=1000.0,
        status=status,
        max_attempts=max_attempts,
        attempt=attempt,
        next_retry_at=next_retry_at,
    )


async def test_recover_on_startup_releases_stale_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess, job_id="j1")
        await claim_job(sess, job_id="j1", owner="worker-a", lease_seconds=10.0, now=1000.0)

    report = await recover_on_startup(session_factory, now=1100.0)
    assert report.released_leases == ["j1"]
    assert report.failed_exhausted == []


async def test_recover_on_startup_fails_exhausted_retries(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(
            sess, job_id="j2", status=JobStatus.RETRY_WAIT, max_attempts=1, attempt=1,
            next_retry_at=900.0,
        )

    report = await recover_on_startup(session_factory, now=1000.0)
    assert report.released_leases == []
    assert report.failed_exhausted == ["j2"]


async def test_recover_on_startup_leaves_healthy_jobs_alone(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess, job_id="queued")
        await _enqueue(sess, job_id="running")
        await claim_job(sess, job_id="running", owner="worker-a", lease_seconds=100.0, now=1000.0)

    report = await recover_on_startup(session_factory, now=1005.0)
    assert report.released_leases == []
    assert report.failed_exhausted == []
