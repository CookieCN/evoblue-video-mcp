"""Job persistence and single-owner lease semantics.

Every state change is written to SQLite before any side effect runs. Ownership is
transferred and advanced with compare-and-swap UPDATEs guarded by status, lease
owner, and lease expiry, so a SQLite single-writer serializes concurrent access:
at most one worker wins a claim, and only the current lease holder may advance it.
"""

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from evoblue_video_mcp.jobs.states import TERMINAL_JOB_STATUSES, JobStatus
from evoblue_video_mcp.jobs.transitions import RUNNING_STATES, validate_transition
from evoblue_video_mcp.storage.models import AppSettings, Job

_TERMINAL_VALUES = [state.value for state in TERMINAL_JOB_STATUSES]
_RUNNING_VALUES = [state.value for state in RUNNING_STATES]

MAX_ATTEMPTS_EXCEEDED = "MAX_ATTEMPTS_EXCEEDED"


class LeaseLostError(RuntimeError):
    """Raised when a worker advances a job whose lease it no longer holds."""


async def _get(session: AsyncSession, job_id: str) -> Job | None:
    return (await session.scalars(select(Job).where(Job.job_id == job_id))).first()


async def get_job(session: AsyncSession, *, job_id: str) -> Job | None:
    """Return the current persisted state of a job, or ``None`` if unknown."""
    return await _get(session, job_id)


async def _get_required(session: AsyncSession, job_id: str) -> Job:
    """Return a job that must exist because we just wrote it."""
    job = await _get(session, job_id)
    if job is None:
        raise RuntimeError(f"Persisted job {job_id!r} vanished after write")
    return job


def _is_claimable(job: Job, now: float) -> bool:
    if job.attempt >= job.max_attempts:
        return False
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
    return and_(
        Job.attempt < Job.max_attempts,
        or_(
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
        ),
    )


async def enqueue_job(
    session: AsyncSession,
    *,
    job_id: str,
    url: str,
    request_fingerprint: str,
    config_fingerprint: str,
    now: float,
    status: JobStatus = JobStatus.QUEUED,
    mode: str = "auto",
    asr: str = "auto",
    language: str | None = None,
    max_attempts: int = 3,
    attempt: int = 0,
    next_retry_at: float | None = None,
) -> Job:
    """Persist a new job in an initial (default ``queued``) state."""
    job = Job(
        job_id=job_id,
        request_fingerprint=request_fingerprint,
        url=url,
        mode=mode,
        asr=asr,
        language=language,
        config_fingerprint=config_fingerprint,
        status=status.value,
        stage=status.value if status in RUNNING_STATES else None,
        max_attempts=max_attempts,
        attempt=attempt,
        next_retry_at=next_retry_at,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    await session.commit()
    return await _get_required(session, job_id)


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
    """Atomically release leases that expired, without racing a fresh take-over."""
    result = await session.execute(
        update(Job)
        .where(
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at <= now,
            Job.status.not_in(_TERMINAL_VALUES),
        )
        .values(lease_owner=None, lease_expires_at=None, updated_at=now)
        .returning(Job.id)
    )
    ids = list(result.scalars().all())
    await session.commit()
    if not ids:
        return []
    return list((await session.scalars(select(Job).where(Job.id.in_(ids)))).all())


async def advance_job(
    session: AsyncSession,
    *,
    job_id: str,
    owner: str,
    to_status: JobStatus,
    now: float,
    lease_seconds: float,
    stage: JobStatus | None = None,
    progress: int | None = None,
) -> Job:
    """Validate, then atomically advance a job only if ``owner`` still holds a live lease."""
    job = await _get(session, job_id)
    if job is None:
        raise KeyError(f"No job with id {job_id!r}")

    validate_transition(JobStatus(job.status), to_status)

    new_stage = job.stage
    if stage is not None:
        new_stage = stage.value
    elif to_status in RUNNING_STATES:
        new_stage = to_status.value

    advance_values: dict[str, object] = {
        "status": to_status.value,
        "stage": new_stage,
        "progress": job.progress if progress is None else progress,
        "updated_at": now,
    }
    if to_status in TERMINAL_JOB_STATUSES:
        # Reaching a terminal state releases the lease; the job is no longer owned.
        advance_values["lease_owner"] = None
        advance_values["lease_expires_at"] = None
    else:
        advance_values["lease_expires_at"] = now + lease_seconds

    result = await session.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.status == job.status,
            Job.lease_owner == owner,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > now,
        )
        .values(advance_values)
        .returning(Job.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise LeaseLostError(
            f"Lease lost for job {job_id!r}; owner {owner!r} no longer holds a live lease"
        )

    await session.commit()
    return await _get_required(session, job_id)


async def fail_exhausted_retries(session: AsyncSession, *, now: float) -> list[Job]:
    """Atomically move retry_wait jobs whose attempts are exhausted into ``failed``."""
    result = await session.execute(
        update(Job)
        .where(
            Job.status == JobStatus.RETRY_WAIT.value,
            Job.attempt >= Job.max_attempts,
        )
        .values(
            status=JobStatus.FAILED.value,
            error_code=MAX_ATTEMPTS_EXCEEDED,
            retryable=False,
            lease_owner=None,
            lease_expires_at=None,
            next_retry_at=None,
            updated_at=now,
        )
        .returning(Job.id)
    )
    ids = list(result.scalars().all())
    await session.commit()
    if not ids:
        return []
    return list((await session.scalars(select(Job).where(Job.id.in_(ids)))).all())


async def mark_failure(
    session: AsyncSession,
    *,
    job_id: str,
    owner: str,
    now: float,
    error_code: str,
    retryable: bool,
    next_retry_at: float | None = None,
) -> Job:
    """Atomically fail a running job into ``retry_wait`` (transient) or ``failed`` (permanent)."""
    job = await _get(session, job_id)
    if job is None:
        raise KeyError(f"No job with id {job_id!r}")

    to_status = JobStatus.RETRY_WAIT if retryable else JobStatus.FAILED
    validate_transition(JobStatus(job.status), to_status)

    result = await session.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.status == job.status,
            Job.lease_owner == owner,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > now,
        )
        .values(
            status=to_status.value,
            error_code=error_code,
            retryable=retryable,
            next_retry_at=next_retry_at if retryable else None,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=now,
        )
        .returning(Job.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise LeaseLostError(
            f"Lease lost for job {job_id!r}; owner {owner!r} no longer holds a live lease"
        )

    await session.commit()
    return await _get_required(session, job_id)


async def list_jobs(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    status: JobStatus | None = None,
) -> tuple[list[Job], int]:
    """Return a page of jobs and the total count matching the optional status filter."""
    filters: list[ColumnElement[bool]] = []
    if status is not None:
        filters.append(Job.status == status.value)

    total = (
        await session.scalar(select(func.count()).select_from(Job).where(*filters))
    ) or 0
    jobs = list(
        (
            await session.scalars(
                select(Job)
                .where(*filters)
                .order_by(Job.created_at.desc(), Job.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return jobs, int(total)


async def get_app_settings(session: AsyncSession) -> AppSettings | None:
    """Return the single app-settings row, or ``None`` before first setup."""
    return (await session.scalars(select(AppSettings).where(AppSettings.id == 1))).first()


async def save_app_settings(
    session: AsyncSession,
    *,
    setup_completed: bool,
    now: float,
    report_directory: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> AppSettings:
    """Persist the single app-settings row (upsert); ``None`` fields clear the value."""
    settings = await get_app_settings(session)
    if settings is None:
        settings = AppSettings(id=1)
        session.add(settings)

    settings.setup_completed = setup_completed
    settings.report_directory = report_directory
    settings.llm_provider = llm_provider
    settings.llm_model = llm_model
    settings.updated_at = now

    await session.commit()
    settings = await get_app_settings(session)
    if settings is None:
        raise RuntimeError("App settings vanished after write")
    return settings
