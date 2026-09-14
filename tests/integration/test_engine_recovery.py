"""Engine startup recovery releases stale leases and fails exhausted retries."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.runtime.engine import recover_on_startup
from evoblue_video_mcp.storage.models import Job
from evoblue_video_mcp.storage.repository import (
    claim_job,
    enqueue_job,
    get_job,
    request_cancellation,
    resume_waiting_jobs,
)


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
        request_fingerprint=f"key-{job_id}",
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


async def test_restart_leaves_waiting_for_model_jobs_waiting(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """F4 (feedback #15): an engine restart alone must neither cancel nor
    resume a parked job — it stays waiting until the install+provider double
    gate passes."""
    async with session_factory() as sess:
        await _enqueue(sess, job_id="wait-1", status=JobStatus.WAITING_FOR_MODEL)
        await sess.execute(
            update(Job)
            .where(Job.job_id == "wait-1")
            .values(asr_recommendation_model_id="sensevoice-small-int8")
        )
        await sess.commit()

    report = await recover_on_startup(session_factory, now=1000.0)
    assert report.released_leases == []
    assert report.failed_exhausted == []
    assert report.landed_cancellations == []

    async with session_factory() as sess:
        job = await get_job(sess, job_id="wait-1")
    assert job is not None
    assert job.status == JobStatus.WAITING_FOR_MODEL.value
    assert job.cancel_requested_at is None


async def test_restart_sweeps_dangling_cancellation_from_waiting(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """F4 (feedback #15): a parked job carrying an unconsumed cancel flag (the
    row shape only pre-F4 versions could produce) is landed as cancelled at
    startup instead of resurrecting on the next model install."""
    async with session_factory() as sess:
        await _enqueue(sess, job_id="legacy", status=JobStatus.WAITING_FOR_MODEL)
        await sess.execute(
            update(Job)
            .where(Job.job_id == "legacy")
            .values(cancel_requested_at=900.0)
        )
        await sess.commit()

    report = await recover_on_startup(session_factory, now=1000.0)
    assert report.landed_cancellations == ["legacy"]

    async with session_factory() as sess:
        job = await get_job(sess, job_id="legacy")
    assert job is not None
    assert job.status == JobStatus.CANCELLED.value
    assert job.error_code == "CANCELLED_BY_USER"


async def test_cancelled_waiting_job_is_not_resurrected_by_resume(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """F4 (feedback #15): an explicitly cancelled job never comes back, while
    its healthy parked sibling for the same model resumes normally."""
    async with session_factory() as sess:
        for job_id in ("wait-2", "wait-3"):
            await _enqueue(sess, job_id=job_id, status=JobStatus.WAITING_FOR_MODEL)
            await sess.execute(
                update(Job)
                .where(Job.job_id == job_id)
                .values(asr_recommendation_model_id="sensevoice-small-int8")
            )
            await sess.commit()
        await request_cancellation(sess, job_id="wait-2", now=1000.0)
        resumed = await resume_waiting_jobs(sess, model_id="sensevoice-small-int8", now=1000.0)

    assert resumed == ["wait-3"]
    async with session_factory() as sess:
        cancelled_job = await get_job(sess, job_id="wait-2")
        healthy_job = await get_job(sess, job_id="wait-3")
    assert cancelled_job is not None and cancelled_job.status == JobStatus.CANCELLED.value
    assert healthy_job is not None and healthy_job.status == JobStatus.TRANSCRIBING.value


async def test_resume_never_resurrects_flagged_waiting_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Defense in depth for the F4 guard: even if a flagged waiting row
    somehow survives every sweep, resume skips it rather than running stages
    for a job the user asked to cancel."""
    async with session_factory() as sess:
        await _enqueue(sess, job_id="wait-4", status=JobStatus.WAITING_FOR_MODEL)
        await sess.execute(
            update(Job)
            .where(Job.job_id == "wait-4")
            .values(
                asr_recommendation_model_id="sensevoice-small-int8",
                cancel_requested_at=900.0,
            )
        )
        await sess.commit()
        resumed = await resume_waiting_jobs(sess, model_id="sensevoice-small-int8", now=1000.0)

    assert resumed == []
    async with session_factory() as sess:
        job = await get_job(sess, job_id="wait-4")
    assert job is not None
    assert job.status == JobStatus.WAITING_FOR_MODEL.value
