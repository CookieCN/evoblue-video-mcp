"""Job persistence and single-owner lease semantics.

Every state change is written to SQLite before any side effect runs. Ownership
is transferred with a compare-and-swap UPDATE guarded by the current status and
lease, so a SQLite single-writer serializes concurrent claims and at most one
worker wins a given job.
"""

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from evoblue_video_mcp.jobs.states import TERMINAL_JOB_STATUSES, JobStatus
from evoblue_video_mcp.jobs.transitions import RUNNING_STATES, validate_transition
from evoblue_video_mcp.storage.models import Job

_TERMINAL_VALUES = [state.value for state in TERMINAL_JOB_STATUSES]
_RUNNING_VALUES = [state.value for state in RUNNING_STATES]


async def _get(session: AsyncSession, job_id: str) -> Job | None:
    return (await session.scalars(select(Job).where(Job.job_id == job_id))).first()


def _is_claimable(job: Job, now: float) -> bool:
    status = JobStatus(job.status)
    if status in TERMINAL_JOB_STATUSES:
        return False
    if status is JobStatus.QUEUED:
        return True
    if status is JobStatus.RETRY_WAIT:
        return job.next_retry_at is not None and job.next_retry_at <= now
    # A running stage whose lease is missing or expired can be taken over.
    return job.lease_expires_at is None or job.lease_expires_at <= now


def _claim_status(job: Job) -> tuple[JobStatus, str | None]:
    status = JobStatus(job.status)
    if status is JobStatus.QUEUED:
        return JobStatus.FETCHING_METADATA, JobStatus.FETCHING_METADATA.value
    if status is JobStatus.RETRY_WAIT:
        stage = job.stage or JobStatus.FETCHING_METADATA.value
        return JobStatus(stage), stage
    return status, status.value


def _claimable_where(now: float) -> ColumnElement[bool]:
    return or_(
        Job.status == JobStatus.QUEUED.value,
        and_(
            Job.status == JobStatus.RETRY_WAIT.value,
            Job.next_retry_at.is_not(None),
            Job.next_retry_at <= now,
        ),
        and_(
            Job.status.in_(_RUNNING_VALUES),
            or_(Job.lease_expires_at.is_(None), Job.lease_expires_at <= now),
        ),
    )


async def enqueue_job(
    session: AsyncSession,
    *,
    job_id: str,
    url: str,
    idempotency_key: str,
    config_fingerprint: str,
    now: float,
    status: JobStatus = JobStatus.QUEUED,
    mode: str = "auto",
    asr: str = "auto",
    language: str | None = None,
    max_attempts: int = 3,
) -> Job:
    """Persist a new job in an initial (default ``queued``) state."""
    job = Job(
        job_id=job_id,
        idempotency_key=idempotency_key,
        url=url,
        mode=mode,
        asr=asr,
        language=language,
        config_fingerprint=config_fingerprint,
        status=status.value,
        stage=status.value if status in RUNNING_STATES else None,
        max_attempts=max_attempts,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    await session.commit()
    return job


async def claim_job(
    session: AsyncSession,
    *,
    job_id: str,
    owner: str,
    lease_seconds: float,
    now: float,
) -> Job | None:
    """Atomically claim one job; returns ``None`` if another owner won or it is unclaimable."""
    job = await _get(session, job_id)
    if job is None or not _is_claimable(job, now):
        return None

    new_status, new_stage = _claim_status(job)
    result = await session.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.status == job.status,
            Job.lease_expires_at == job.lease_expires_at,
        )
        .values(
            status=new_status.value,
            stage=new_stage,
            lease_owner=owner,
            lease_expires_at=now + lease_seconds,
            attempt=Job.attempt + 1,
            updated_at=now,
        )
        .returning(Job.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        return None

    await session.commit()
    return await _get(session, job_id)


async def claim_next_job(
    session: AsyncSession,
    *,
    owner: str,
    lease_seconds: float,
    now: float,
) -> Job | None:
    """Claim the oldest claimable job, or ``None`` when nothing is ready."""
    candidates = list(
        (
            await session.scalars(
                select(Job).where(_claimable_where(now)).order_by(Job.created_at, Job.id).limit(16)
            )
        ).all()
    )
    for candidate in candidates:
        claimed = await claim_job(
            session,
            job_id=candidate.job_id,
            owner=owner,
            lease_seconds=lease_seconds,
            now=now,
        )
        if claimed is not None:
            return claimed
    return None


async def recover_stale_jobs(session: AsyncSession, *, now: float) -> list[Job]:
    """Release leases that expired while a worker was gone, so jobs can be reclaimed."""
    jobs = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.lease_expires_at.is_not(None),
                    Job.lease_expires_at <= now,
                    Job.status.not_in(_TERMINAL_VALUES),
                )
            )
        ).all()
    )
    for job in jobs:
        job.lease_owner = None
        job.lease_expires_at = None
        job.updated_at = now
    if jobs:
        await session.commit()
    return jobs


async def advance_job(
    session: AsyncSession,
    *,
    job_id: str,
    to_status: JobStatus,
    now: float,
    stage: JobStatus | None = None,
    progress: int | None = None,
) -> Job:
    """Validate then persist a state transition before any side effect runs."""
    job = await _get(session, job_id)
    if job is None:
        raise KeyError(f"No job with id {job_id!r}")

    src = JobStatus(job.status)
    validate_transition(src, to_status)

    job.status = to_status.value
    if stage is not None:
        job.stage = stage.value
    elif to_status in RUNNING_STATES:
        job.stage = to_status.value
    if progress is not None:
        job.progress = progress
    job.updated_at = now

    await session.commit()
    return job
