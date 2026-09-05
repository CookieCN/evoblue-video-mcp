"""Job persistence and single-owner lease semantics.

Every state change is written to SQLite before any side effect runs. Ownership is
transferred and advanced with compare-and-swap UPDATEs guarded by status, lease
owner, and lease expiry, so a SQLite single-writer serializes concurrent access:
at most one worker wins a claim, and only the current lease holder may advance it.
"""

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from evoblue_video_mcp.jobs.states import TERMINAL_JOB_STATUSES, JobStatus
from evoblue_video_mcp.jobs.transitions import RUNNING_STATES, validate_transition
from evoblue_video_mcp.storage.db import ensure_immediate_transaction
from evoblue_video_mcp.storage.model_download import (
    ACTIVE_DOWNLOAD_STATUSES,
    ModelDownloadStatus,
)
from evoblue_video_mcp.storage.model_download import (
    validate_transition as validate_download_transition,
)
from evoblue_video_mcp.storage.models import (
    ActiveModel,
    AppSettings,
    Job,
    JobArtifact,
    ModelDownload,
    ModelInstall,
)

_TERMINAL_VALUES = [state.value for state in TERMINAL_JOB_STATUSES]
_RUNNING_VALUES = [state.value for state in RUNNING_STATES]

MAX_ATTEMPTS_EXCEEDED = "MAX_ATTEMPTS_EXCEEDED"


async def _begin_immediate(session: AsyncSession) -> None:
    """Start this writer's transaction as ``BEGIN IMMEDIATE`` (§6).

    Thin delegation to ``db.ensure_immediate_transaction`` — see it for the
    exact transaction-state semantics (idempotent inside an IMMEDIATE
    transaction; refuses a DEFERRED transaction that has executed DML as
    tracked at the engine event layer; closes a read-only snapshot with an
    empty commit).
    """
    await ensure_immediate_transaction(session)


class LeaseLostError(RuntimeError):
    """Raised when a worker advances a job whose lease it no longer holds."""


class StaleRevisionError(RuntimeError):
    """Raised when a download operation is advanced with an outdated revision."""


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
    await _begin_immediate(session)
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
    await _begin_immediate(session)
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
    await _begin_immediate(session)
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
    await _begin_immediate(session)
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
    await _begin_immediate(session)
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
        "error_code": None,
        "error_detail": None,
        "retryable": False,
        "updated_at": now,
    }
    if to_status in TERMINAL_JOB_STATUSES or to_status is JobStatus.WAITING_FOR_MODEL:
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
    await _begin_immediate(session)
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
    error_detail: str | None = None,
) -> Job:
    """Atomically fail a running job into ``retry_wait`` (transient) or ``failed`` (permanent)."""
    await _begin_immediate(session)
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
            error_detail=error_detail,
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


async def pin_job_asr_route(
    session: AsyncSession,
    *,
    job_id: str,
    owner: str,
    now: float,
    provider_id: str,
    model_id: str,
    model_version: str,
    recommendation_model_id: str | None,
) -> Job:
    """Persist the selected ASR identity under the current worker lease."""
    await _begin_immediate(session)
    job = await _get(session, job_id)
    if job is None:
        raise KeyError(f"No job with id {job_id!r}")
    result = await session.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.status == JobStatus.TRANSCRIBING.value,
            Job.lease_owner == owner,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > now,
        )
        .values(
            asr_provider_id=provider_id,
            asr_model_id=model_id,
            asr_model_version=model_version,
            asr_recommendation_model_id=recommendation_model_id,
            updated_at=now,
        )
        .returning(Job.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise LeaseLostError(f"Lease lost while pinning ASR route for job {job_id!r}")
    await session.commit()
    return await _get_required(session, job_id)


async def list_waiting_model_ids(session: AsyncSession) -> list[str]:
    """Return the distinct recommendation model ids of jobs parked for a model."""
    result = await session.execute(
        select(Job.asr_recommendation_model_id)
        .where(
            Job.status == JobStatus.WAITING_FOR_MODEL.value,
            Job.asr_recommendation_model_id.is_not(None),
        )
        .distinct()
    )
    return [model_id for model_id in result.scalars().all() if model_id is not None]


async def resume_waiting_jobs(
    session: AsyncSession, *, model_id: str, now: float
) -> list[str]:
    """Resume jobs waiting for an explicitly installed model."""
    await _begin_immediate(session)
    result = await session.execute(
        update(Job)
        .where(
            Job.status == JobStatus.WAITING_FOR_MODEL.value,
            Job.asr_recommendation_model_id == model_id,
        )
        .values(
            status=JobStatus.TRANSCRIBING.value,
            stage=JobStatus.TRANSCRIBING.value,
            asr_recommendation_model_id=None,
            error_code=None,
            error_detail=None,
            retryable=False,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=now,
        )
        .returning(Job.job_id)
    )
    job_ids = list(result.scalars().all())
    await session.commit()
    return job_ids


async def save_app_settings(
    session: AsyncSession,
    *,
    setup_completed: bool,
    now: float,
    report_directory: str | None = None,
    llm_provider: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
    llm_credential_ref: str | None = None,
    asr_provider: str | None = None,
    whisper_cpp_executable: str | None = None,
    commit: bool = True,
) -> AppSettings:
    """Persist the single app-settings row (upsert); ``None`` fields clear the value.

    With ``commit=False`` the caller owns the transaction: nothing is
    committed and the in-memory object is returned WITHOUT the post-commit
    re-read. The settings PUT needs this to coordinate the recovery-pointer
    dual-write (INDEX_REBUILD §4): its commit runs as the LAST step of the
    endpoint's transaction, so "the endpoint saw an exception" ⇒ "the
    database did not adopt the value" — the pointer compensation is exact
    and a post-commit failure can never be mistaken for a failed save.
    """
    await _begin_immediate(session)
    settings = await get_app_settings(session)
    if settings is None:
        settings = AppSettings(id=1)
        session.add(settings)

    settings.setup_completed = setup_completed
    settings.report_directory = report_directory
    settings.llm_provider = llm_provider
    settings.llm_base_url = llm_base_url
    settings.llm_model = llm_model
    settings.llm_credential_ref = llm_credential_ref
    settings.asr_provider = asr_provider
    settings.whisper_cpp_executable = whisper_cpp_executable
    settings.updated_at = now
    if not commit:
        return settings

    await session.commit()
    settings = await get_app_settings(session)
    if settings is None:
        raise RuntimeError("App settings vanished after write")
    return settings


async def commit_artifact_and_advance(
    session: AsyncSession,
    *,
    job_id: str,
    owner: str,
    to_status: JobStatus,
    now: float,
    lease_seconds: float,
    stage: str,
    artifact_type: str,
    input_fingerprint: str,
    schema_version: int,
    storage_kind: str,
    payload_json: str | None = None,
    relative_path: str | None = None,
    content_hash: str | None = None,
    byte_size: int | None = None,
    progress: int | None = None,
) -> Job:
    """Register a stage artifact and advance the job in one transaction.

    The artifact row and the CAS job update commit together, so a crash cannot
    leave an artifact without state advancement or the reverse. Re-registering
    an existing ``(job_id, artifact_type, input_fingerprint)`` is a no-op.
    """
    await _begin_immediate(session)
    job = await _get(session, job_id)
    if job is None:
        raise KeyError(f"No job with id {job_id!r}")

    validate_transition(JobStatus(job.status), to_status)

    existing = (
        await session.scalars(
            select(JobArtifact).where(
                JobArtifact.job_id == job_id,
                JobArtifact.artifact_type == artifact_type,
                JobArtifact.input_fingerprint == input_fingerprint,
            )
        )
    ).first()
    if existing is None:
        session.add(
            JobArtifact(
                job_id=job_id,
                stage=stage,
                artifact_type=artifact_type,
                input_fingerprint=input_fingerprint,
                schema_version=schema_version,
                storage_kind=storage_kind,
                payload_json=payload_json,
                relative_path=relative_path,
                content_hash=content_hash,
                byte_size=byte_size,
                created_at=now,
                updated_at=now,
            )
        )

    new_stage = to_status.value if to_status in RUNNING_STATES else job.stage
    advance_values: dict[str, object] = {
        "status": to_status.value,
        "stage": new_stage,
        "progress": job.progress if progress is None else progress,
        "error_code": None,
        "error_detail": None,
        "retryable": False,
        "updated_at": now,
    }
    if to_status in TERMINAL_JOB_STATUSES:
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


async def get_artifact(
    session: AsyncSession,
    *,
    job_id: str,
    artifact_type: str,
    input_fingerprint: str,
) -> JobArtifact | None:
    """Return a registered artifact, or ``None`` if not present."""
    return (
        await session.scalars(
            select(JobArtifact).where(
                JobArtifact.job_id == job_id,
                JobArtifact.artifact_type == artifact_type,
                JobArtifact.input_fingerprint == input_fingerprint,
            )
        )
    ).first()


async def renew_lease(
    session: AsyncSession,
    *,
    job_id: str,
    owner: str,
    now: float,
    lease_seconds: float,
) -> bool:
    """Renew a live lease; return False if the lease was lost."""
    await _begin_immediate(session)
    result = await session.execute(
        update(Job)
        .where(
            Job.job_id == job_id,
            Job.lease_owner == owner,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > now,
        )
        .values(lease_expires_at=now + lease_seconds, updated_at=now)
        .returning(Job.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        return False
    await session.commit()
    return True


async def is_cancel_requested(session: AsyncSession, *, job_id: str) -> bool:
    """Return True if the job has a pending cancellation request."""
    job = await _get(session, job_id)
    return job is not None and job.cancel_requested_at is not None


async def save_artifact_inline(
    session: AsyncSession,
    *,
    job_id: str,
    owner: str,
    now: float,
    stage: str,
    artifact_type: str,
    input_fingerprint: str,
    schema_version: int,
    payload_json: str,
) -> None:
    """Persist a checkpoint only while the caller still holds a live lease."""
    await _begin_immediate(session)
    lease_guard = await session.execute(
        update(Job)
        .where(
            Job.job_id == job_id,
            Job.lease_owner == owner,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > now,
        )
        .values(updated_at=Job.updated_at)
        .returning(Job.id)
    )
    if lease_guard.scalar_one_or_none() is None:
        await session.rollback()
        raise LeaseLostError(f"lease lost for job {job_id!r}")

    existing = await get_artifact(
        session, job_id=job_id, artifact_type=artifact_type, input_fingerprint=input_fingerprint
    )
    if existing is not None:
        return
    session.add(
        JobArtifact(
            job_id=job_id,
            stage=stage,
            artifact_type=artifact_type,
            input_fingerprint=input_fingerprint,
            schema_version=schema_version,
            storage_kind="inline_json",
            payload_json=payload_json,
            created_at=now,
            updated_at=now,
        )
    )
    await session.commit()


async def mark_cancelled(
    session: AsyncSession,
    *,
    job_id: str,
    owner: str,
    now: float,
) -> Job:
    """Atomically transition a running job into ``cancelled``."""
    await _begin_immediate(session)
    job = await _get(session, job_id)
    if job is None:
        raise KeyError(f"No job with id {job_id!r}")

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
            status=JobStatus.CANCELLED.value,
            error_code="CANCELLED_BY_USER",
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


async def request_cancellation(session: AsyncSession, *, job_id: str, now: float) -> Job | None:
    """Cancel an idle job immediately or flag a running job for a safe-point stop."""
    await _begin_immediate(session)
    job = await _get(session, job_id)
    if job is None:
        return None
    status = JobStatus(job.status)
    if status in TERMINAL_JOB_STATUSES:
        return job

    values: dict[str, object] = {"cancel_requested_at": now, "updated_at": now}
    if status in {JobStatus.QUEUED, JobStatus.RETRY_WAIT, JobStatus.WAITING_FOR_MODEL}:
        values.update(
            status=JobStatus.CANCELLED.value,
            error_code="CANCELLED_BY_USER",
            retryable=False,
            next_retry_at=None,
            lease_owner=None,
            lease_expires_at=None,
        )

    result = await session.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == job.status)
        .values(values)
        .returning(Job.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        return await _get(session, job_id)
    await session.commit()
    return await _get_required(session, job_id)


async def get_active_model(session: AsyncSession, *, model_id: str) -> ActiveModel | None:
    """Return the active-version pointer for a model, or ``None``."""
    return (
        await session.scalars(select(ActiveModel).where(ActiveModel.model_id == model_id))
    ).first()


async def activate_installation(
    session: AsyncSession, *, model_id: str, version: str, now: float
) -> ActiveModel:
    """Activate an installed version; fails without touching active if not installed."""
    await _begin_immediate(session)
    install = await get_installation(session, model_id=model_id, version=version)
    if install is None:
        raise KeyError(f"cannot activate {model_id!r}/{version!r}: not installed")
    active = await get_active_model(session, model_id=model_id)
    if active is None:
        active = ActiveModel(model_id=model_id, active_version=version, updated_at=now)
        session.add(active)
    active.active_version = version
    active.updated_at = now
    await session.commit()
    result = await get_active_model(session, model_id=model_id)
    if result is None:
        raise RuntimeError(f"active model {model_id!r} vanished after write")
    return result


async def complete_installation(
    session: AsyncSession,
    *,
    operation_id: str,
    model_id: str,
    version: str,
    installed_path: str,
    now: float,
) -> ModelDownload:
    """Record + activate the install and complete the operation in one transaction.

    All three effects commit together, so a failure (including a concurrent
    revision bump on the operation) rolls back and leaves the previous active
    version untouched.
    """
    await _begin_immediate(session)
    operation = await get_download_operation(session, operation_id=operation_id)
    if operation is None:
        raise KeyError(f"No download operation with id {operation_id!r}")
    if operation.model_id != model_id or operation.version != version:
        raise ValueError(
            f"operation {operation_id!r} is for {operation.model_id!r}/{operation.version!r}, "
            f"not {model_id!r}/{version!r}"
        )
    validate_download_transition(
        ModelDownloadStatus(operation.status), ModelDownloadStatus.COMPLETED
    )

    install = await get_installation(session, model_id=model_id, version=version)
    if install is None:
        install = ModelInstall(
            model_id=model_id, version=version, installed_path=installed_path, installed_at=now
        )
        session.add(install)
    install.installed_path = installed_path
    install.installed_at = now

    active = await get_active_model(session, model_id=model_id)
    if active is None:
        active = ActiveModel(model_id=model_id, active_version=version, updated_at=now)
        session.add(active)
    active.active_version = version
    active.updated_at = now

    result = await session.execute(
        update(ModelDownload)
        .where(
            ModelDownload.operation_id == operation_id,
            ModelDownload.revision == operation.revision,
        )
        .values(
            status=ModelDownloadStatus.COMPLETED.value,
            revision=operation.revision + 1,
            error_code=None,
            error_detail=None,
            updated_at=now,
        )
        .returning(ModelDownload.operation_id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise StaleRevisionError(
            f"download operation {operation_id!r} was updated concurrently"
        )
    await session.commit()
    updated = await get_download_operation(session, operation_id=operation_id)
    if updated is None:
        raise RuntimeError(f"download operation {operation_id!r} vanished after write")
    return updated


async def get_installation(
    session: AsyncSession, *, model_id: str, version: str
) -> ModelInstall | None:
    """Return one installed model version, or ``None``."""
    return (
        await session.scalars(
            select(ModelInstall).where(
                ModelInstall.model_id == model_id, ModelInstall.version == version
            )
        )
    ).first()


async def get_installations(
    session: AsyncSession, *, model_id: str | None = None
) -> list[ModelInstall]:
    """Return installed versions, optionally limited to one model.

    Used by uninstall trash reconciliation: a leftover trash entry whose model
    still has an install record must be restored rather than deleted.
    """
    statement = select(ModelInstall)
    if model_id is not None:
        statement = statement.where(ModelInstall.model_id == model_id)
    return list((await session.scalars(statement)).all())


async def save_installation(
    session: AsyncSession, *, model_id: str, version: str, installed_path: str, now: float
) -> ModelInstall:
    """Record an installed model version; idempotent for the same (model, version)."""
    await _begin_immediate(session)
    install = await get_installation(session, model_id=model_id, version=version)
    if install is None:
        install = ModelInstall(
            model_id=model_id,
            version=version,
            installed_path=installed_path,
            installed_at=now,
        )
        session.add(install)
    install.installed_path = installed_path
    install.installed_at = now
    await session.commit()
    result = await get_installation(session, model_id=model_id, version=version)
    if result is None:
        raise RuntimeError(f"installation {model_id!r}/{version!r} vanished after write")
    return result


async def create_download_operation(
    session: AsyncSession,
    *,
    operation_id: str,
    model_id: str,
    version: str,
    source_url: str,
    source_kind: str,
    expected_sha256: str,
    expected_size_bytes: int,
    now: float,
    etag: str | None = None,
    last_modified: str | None = None,
) -> ModelDownload:
    """Create a download operation in ``pending`` state.

    ``etag``/``last_modified`` may carry validators inherited from a prior attempt
    for the same (model, version), so a resume can send ``If-Range`` immediately.
    """
    await _begin_immediate(session)
    operation = ModelDownload(
        operation_id=operation_id,
        model_id=model_id,
        version=version,
        source_url=source_url,
        source_kind=source_kind,
        expected_sha256=expected_sha256,
        expected_size_bytes=expected_size_bytes,
        downloaded_bytes=0,
        etag=etag,
        last_modified=last_modified,
        status=ModelDownloadStatus.PENDING.value,
        revision=0,
        created_at=now,
        updated_at=now,
    )
    session.add(operation)
    await session.commit()
    result = await get_download_operation(session, operation_id=operation_id)
    if result is None:
        raise RuntimeError(f"download operation {operation_id!r} vanished after write")
    return result


async def find_latest_download_operation(
    session: AsyncSession, *, model_id: str, version: str
) -> ModelDownload | None:
    """Return the most recent download operation for a (model, version), or ``None``."""
    return (
        await session.scalars(
            select(ModelDownload)
            .where(ModelDownload.model_id == model_id, ModelDownload.version == version)
            .order_by(ModelDownload.created_at.desc(), ModelDownload.revision.desc())
            .limit(1)
        )
    ).first()


async def get_download_operation(
    session: AsyncSession, *, operation_id: str
) -> ModelDownload | None:
    """Return one download operation, or ``None``."""
    return (
        await session.scalars(
            select(ModelDownload).where(ModelDownload.operation_id == operation_id)
        )
    ).first()


async def find_active_download_operation(
    session: AsyncSession, *, model_id: str
) -> ModelDownload | None:
    """Return the single non-terminal download operation for a model, or ``None``."""
    active_values = [status.value for status in ACTIVE_DOWNLOAD_STATUSES]
    return (
        await session.scalars(
            select(ModelDownload).where(
                ModelDownload.model_id == model_id,
                ModelDownload.status.in_(active_values),
            )
        )
    ).first()


async def restart_download(
    session: AsyncSession, *, operation_id: str, now: float
) -> ModelDownload:
    """Zero the byte count and validators so a fresh download restarts safely.

    Only allowed while ``pending`` or ``downloading``. Used when the on-disk
    partial is inconsistent with the recorded progress, or when the server
    ignored a Range request and the resource is being rewritten from zero.
    """
    await _begin_immediate(session)
    operation = await get_download_operation(session, operation_id=operation_id)
    if operation is None:
        raise KeyError(f"No download operation with id {operation_id!r}")
    if operation.status not in {
        ModelDownloadStatus.PENDING.value,
        ModelDownloadStatus.DOWNLOADING.value,
    }:
        raise ValueError("restart is only allowed while pending or downloading")
    result = await session.execute(
        update(ModelDownload)
        .where(
            ModelDownload.operation_id == operation_id,
            ModelDownload.revision == operation.revision,
        )
        .values(
            downloaded_bytes=0,
            etag=None,
            last_modified=None,
            revision=operation.revision + 1,
            updated_at=now,
        )
        .returning(ModelDownload.operation_id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise StaleRevisionError(
            f"download operation {operation_id!r} was updated concurrently"
        )
    await session.commit()
    updated = await get_download_operation(session, operation_id=operation_id)
    if updated is None:
        raise RuntimeError(f"download operation {operation_id!r} vanished after write")
    return updated


async def advance_download_operation(
    session: AsyncSession,
    *,
    operation_id: str,
    to_status: ModelDownloadStatus,
    now: float,
    temp_path: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    expected_revision: int | None = None,
) -> ModelDownload:
    """Transition a download operation with an optimistic revision check.

    Byte-count changes go through ``update_download_progress`` only; entering
    ``verifying`` requires the download to be complete.
    """
    await _begin_immediate(session)
    operation = await get_download_operation(session, operation_id=operation_id)
    if operation is None:
        raise KeyError(f"No download operation with id {operation_id!r}")
    validate_download_transition(ModelDownloadStatus(operation.status), to_status)
    if (
        to_status == ModelDownloadStatus.VERIFYING
        and operation.downloaded_bytes != operation.expected_size_bytes
    ):
        raise ValueError("cannot enter verifying before the download is complete")
    revision = operation.revision if expected_revision is None else expected_revision

    values: dict[str, object] = {
        "status": to_status.value,
        "revision": revision + 1,
        "updated_at": now,
    }
    if temp_path is not None:
        values["temp_path"] = temp_path
    if etag is not None:
        values["etag"] = etag
    if last_modified is not None:
        values["last_modified"] = last_modified
    if error_code is not None:
        values["error_code"] = error_code
    if error_detail is not None:
        values["error_detail"] = error_detail

    result = await session.execute(
        update(ModelDownload)
        .where(
            ModelDownload.operation_id == operation_id,
            ModelDownload.revision == revision,
        )
        .values(values)
        .returning(ModelDownload.operation_id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise StaleRevisionError(
            f"download operation {operation_id!r} was updated concurrently"
        )
    await session.commit()
    updated = await get_download_operation(session, operation_id=operation_id)
    if updated is None:
        raise RuntimeError(f"download operation {operation_id!r} vanished after write")
    return updated


async def update_download_progress(
    session: AsyncSession,
    *,
    operation_id: str,
    downloaded_bytes: int,
    now: float,
    temp_path: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    expected_revision: int | None = None,
) -> ModelDownload:
    """Persist download progress bytes with monotonic, bounded, revision-CAS updates.

    Only allowed while ``downloading``; never changes source/SHA/status/operation.
    """
    await _begin_immediate(session)
    operation = await get_download_operation(session, operation_id=operation_id)
    if operation is None:
        raise KeyError(f"No download operation with id {operation_id!r}")
    if operation.status != ModelDownloadStatus.DOWNLOADING.value:
        raise ValueError("progress may only be updated while downloading")
    if downloaded_bytes < operation.downloaded_bytes:
        raise ValueError("downloaded_bytes must not decrease")
    if not 0 <= downloaded_bytes <= operation.expected_size_bytes:
        raise ValueError("downloaded_bytes out of range")
    revision = operation.revision if expected_revision is None else expected_revision

    values: dict[str, object] = {
        "downloaded_bytes": downloaded_bytes,
        "revision": revision + 1,
        "updated_at": now,
    }
    if temp_path is not None:
        values["temp_path"] = temp_path
    if etag is not None:
        values["etag"] = etag
    if last_modified is not None:
        values["last_modified"] = last_modified

    result = await session.execute(
        update(ModelDownload)
        .where(
            ModelDownload.operation_id == operation_id,
            ModelDownload.revision == revision,
        )
        .values(values)
        .returning(ModelDownload.operation_id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise StaleRevisionError(
            f"download operation {operation_id!r} was updated concurrently"
        )
    await session.commit()
    updated = await get_download_operation(session, operation_id=operation_id)
    if updated is None:
        raise RuntimeError(f"download operation {operation_id!r} vanished after write")
    return updated


async def switch_download_source(
    session: AsyncSession,
    *,
    operation_id: str,
    source_url: str,
    source_kind: str,
    now: float,
) -> ModelDownload:
    """Point an in-flight download at the next source, restarting from zero.

    A failed source may have left a corrupt byte prefix; without per-chunk hashes a
    cross-source resume is unsafe, so the byte count and source-specific validators
    are reset and the caller must truncate the on-disk partial before retrying.
    Allowed only while ``pending`` or ``downloading``.
    """
    await _begin_immediate(session)
    operation = await get_download_operation(session, operation_id=operation_id)
    if operation is None:
        raise KeyError(f"No download operation with id {operation_id!r}")
    if operation.status not in {
        ModelDownloadStatus.PENDING.value,
        ModelDownloadStatus.DOWNLOADING.value,
    }:
        raise ValueError("source may only be switched while pending or downloading")
    result = await session.execute(
        update(ModelDownload)
        .where(
            ModelDownload.operation_id == operation_id,
            ModelDownload.revision == operation.revision,
        )
        .values(
            source_url=source_url,
            source_kind=source_kind,
            downloaded_bytes=0,
            etag=None,
            last_modified=None,
            revision=operation.revision + 1,
            updated_at=now,
        )
        .returning(ModelDownload.operation_id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise StaleRevisionError(
            f"download operation {operation_id!r} was updated concurrently"
        )
    await session.commit()
    updated = await get_download_operation(session, operation_id=operation_id)
    if updated is None:
        raise RuntimeError(f"download operation {operation_id!r} vanished after write")
    return updated


async def uninstall_model(session: AsyncSession, *, model_id: str) -> list[str]:
    """Delete every install/active/download record for a model, returning removed install paths.

    The caller owns filesystem removal and reclaimable-space reporting; this only
    clears persisted state atomically so a crash cannot leave orphaned DB rows
    without directories or the reverse.
    """
    await _begin_immediate(session)
    installs = list(
        (
            await session.scalars(
                select(ModelInstall).where(ModelInstall.model_id == model_id)
            )
        ).all()
    )
    paths = [install.installed_path for install in installs]
    await session.execute(delete(ModelInstall).where(ModelInstall.model_id == model_id))
    await session.execute(delete(ActiveModel).where(ActiveModel.model_id == model_id))
    await session.execute(delete(ModelDownload).where(ModelDownload.model_id == model_id))
    await session.commit()
    return paths
