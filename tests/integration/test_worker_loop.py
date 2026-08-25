"""Worker loop drives the state machine: advance, fail, complete, and idle."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.runtime.worker import StageOutcome, run_worker_once
from evoblue_video_mcp.storage.models import Job
from evoblue_video_mcp.storage.repository import enqueue_job, get_job


class _ForwardHandler:
    def __init__(self, target: JobStatus) -> None:
        self._target = target

    async def execute(self, job: Job) -> StageOutcome:
        return StageOutcome.success(self._target)


class _TransientHandler:
    async def execute(self, job: Job) -> StageOutcome:
        return StageOutcome.transient("SUBTITLE_UNAVAILABLE", next_retry_at=1500.0)


class _FatalHandler:
    async def execute(self, job: Job) -> StageOutcome:
        return StageOutcome.fatal("LLM_NOT_CONFIGURED")


_PIPELINE_CHAIN = [
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


async def _enqueue(session: AsyncSession, job_id: str = "job-1") -> None:
    await enqueue_job(
        session,
        job_id=job_id,
        url="https://www.youtube.com/watch?v=abc",
        request_fingerprint=f"key-{job_id}",
        config_fingerprint="fp-1",
        now=1000.0,
    )


async def test_worker_runs_full_pipeline_to_completion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)

    handlers = {src: _ForwardHandler(dst) for src, dst in _PIPELINE_CHAIN}
    job = await run_worker_once(
        session_factory, owner="worker-a", lease_seconds=30.0, now=1000.0, handlers=handlers
    )
    assert job is not None
    assert job.status == JobStatus.COMPLETED.value
    assert job.lease_owner is None


async def test_worker_transient_failure_enters_retry_wait(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)

    job = await run_worker_once(
        session_factory,
        owner="worker-a",
        lease_seconds=30.0,
        now=1000.0,
        handlers={JobStatus.FETCHING_METADATA: _TransientHandler()},
    )
    assert job is not None
    assert job.status == JobStatus.RETRY_WAIT.value
    assert job.error_code == "SUBTITLE_UNAVAILABLE"
    assert job.next_retry_at == 1500.0
    assert job.lease_owner is None


async def test_worker_fatal_failure_enters_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)

    job = await run_worker_once(
        session_factory,
        owner="worker-a",
        lease_seconds=30.0,
        now=1000.0,
        handlers={JobStatus.FETCHING_METADATA: _FatalHandler()},
    )
    assert job is not None
    assert job.status == JobStatus.FAILED.value
    assert job.error_code == "LLM_NOT_CONFIGURED"


async def test_worker_idle_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job = await run_worker_once(
        session_factory, owner="worker-a", lease_seconds=30.0, now=1000.0, handlers={}
    )
    assert job is None


async def test_worker_missing_handler_marks_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)

    job = await run_worker_once(
        session_factory, owner="worker-a", lease_seconds=30.0, now=1000.0, handlers={}
    )
    assert job is not None
    assert job.status == JobStatus.FAILED.value
    assert job.error_code == "INTERNAL_ERROR"


async def test_completed_job_is_not_reclaimed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)
        handlers = {src: _ForwardHandler(dst) for src, dst in _PIPELINE_CHAIN}
        await run_worker_once(
            session_factory, owner="worker-a", lease_seconds=30.0, now=1000.0, handlers=handlers
        )

    async with session_factory() as sess:
        job = await get_job(sess, job_id="job-1")
        assert job is not None
        assert job.status == JobStatus.COMPLETED.value
