"""Worker execution loop: claim a job, run its stage handler, advance state.

A worker claims one job and drives it stage-by-stage until it reaches a terminal
state, pauses into ``retry_wait``, or fails. Handlers receive a ``StageContext``
so long-running stages can renew their lease and check for cancellation between
units of work. The loop uses a real clock by default, injectable for tests.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.jobs import TERMINAL_JOB_STATUSES, JobStatus
from evoblue_video_mcp.storage.models import Job
from evoblue_video_mcp.storage.repository import (
    LeaseLostError,
    advance_job,
    claim_next_job,
    commit_artifact_and_advance,
    get_job,
    is_cancel_requested,
    mark_cancelled,
    mark_failure,
    renew_lease,
)

MISSING_HANDLER_ERROR = "INTERNAL_ERROR"
DEFAULT_RETRY_DELAY = 60.0


def _sanitize_error(exc: Exception) -> str:
    """Return a safe description of an unexpected exception, never its message."""
    return type(exc).__name__


@dataclass(frozen=True)
class ArtifactRecord:
    """A stage artifact to register atomically with the state transition."""

    stage: str
    artifact_type: str
    input_fingerprint: str
    schema_version: int
    storage_kind: str
    payload_json: str | None = None
    relative_path: str | None = None
    content_hash: str | None = None
    byte_size: int | None = None


@dataclass(frozen=True)
class StageOutcome:
    """Result of one stage: advance (with optional artifact) or fail."""

    target: JobStatus | None = None
    progress: int | None = None
    artifact: ArtifactRecord | None = None
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool = False
    next_retry_at: float | None = None

    @classmethod
    def success(
        cls,
        target: JobStatus,
        progress: int | None = None,
        artifact: ArtifactRecord | None = None,
    ) -> "StageOutcome":
        return cls(target=target, progress=progress, artifact=artifact)

    @classmethod
    def transient(
        cls,
        error_code: str,
        next_retry_at: float | None = None,
        error_detail: str | None = None,
    ) -> "StageOutcome":
        return cls(
            error_code=error_code,
            error_detail=error_detail,
            retryable=True,
            next_retry_at=next_retry_at,
        )

    @classmethod
    def fatal(cls, error_code: str, error_detail: str | None = None) -> "StageOutcome":
        return cls(error_code=error_code, error_detail=error_detail)


@dataclass(frozen=True)
class StageContext:
    """Services a stage can use to renew its lease and observe cancellation."""

    owner: str
    lease_seconds: float
    now: Callable[[], float]
    renew_lease: Callable[[], Awaitable[bool]]
    is_cancelled: Callable[[], Awaitable[bool]]


class StageHandler(Protocol):
    async def execute(self, job: Job, session: AsyncSession, ctx: StageContext) -> StageOutcome: ...


async def run_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: float,
    now_fn: Callable[[], float] | None = None,
    handlers: Mapping[JobStatus, StageHandler],
) -> Job | None:
    """Claim one job and drive it through its stages; returns the job or ``None`` if idle."""
    now = now_fn or time.time
    async with session_factory() as sess:
        job = await claim_next_job(sess, owner=owner, lease_seconds=lease_seconds, now=now())
        if job is None:
            return None
        job_id = job.job_id

        async def renew_current_lease() -> bool:
            async with session_factory() as heartbeat_session:
                return await renew_lease(
                    heartbeat_session,
                    job_id=job_id,
                    owner=owner,
                    now=now(),
                    lease_seconds=lease_seconds,
                )

        async def cancellation_requested() -> bool:
            async with session_factory() as cancellation_session:
                return await is_cancel_requested(cancellation_session, job_id=job_id)

        ctx = StageContext(
            owner=owner,
            lease_seconds=lease_seconds,
            now=now,
            renew_lease=renew_current_lease,
            is_cancelled=cancellation_requested,
        )

        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            _heartbeat_lease(
                session_factory,
                job_id=job_id,
                owner=owner,
                lease_seconds=lease_seconds,
                now=now,
                stop=heartbeat_stop,
            )
        )

        try:
            while True:
                status = JobStatus(job.status)
                if status in TERMINAL_JOB_STATUSES:
                    break

                handler = handlers.get(status)
                if handler is None:
                    heartbeat_stop.set()
                    await heartbeat
                    await mark_failure(
                        sess,
                        job_id=job.job_id,
                        owner=owner,
                        now=now(),
                        error_code=MISSING_HANDLER_ERROR,
                        retryable=False,
                        error_detail=f"no handler for stage {status.value}",
                    )
                    break

                # Claim/get operations leave a read transaction open. Close it before
                # an external call so the separate heartbeat session can write.
                await sess.commit()
                try:
                    outcome = await handler.execute(job, sess, ctx)
                except Exception as exc:
                    heartbeat_stop.set()
                    await heartbeat
                    # CancelledError is not an Exception, so shutdown still propagates.
                    await mark_failure(
                        sess,
                        job_id=job.job_id,
                        owner=owner,
                        now=now(),
                        error_code="INTERNAL_ERROR",
                        retryable=False,
                        error_detail=_sanitize_error(exc),
                    )
                    break
                heartbeat_stop.set()
                await heartbeat
                await sess.refresh(job)

                if outcome.error_code == "CANCELLED_BY_USER":
                    await mark_cancelled(sess, job_id=job.job_id, owner=owner, now=now())
                    break

                if outcome.error_code is not None:
                    next_retry_at = outcome.next_retry_at
                    if next_retry_at is None and outcome.retryable:
                        next_retry_at = now() + DEFAULT_RETRY_DELAY
                    await mark_failure(
                        sess,
                        job_id=job.job_id,
                        owner=owner,
                        now=now(),
                        error_code=outcome.error_code,
                        retryable=outcome.retryable,
                        next_retry_at=next_retry_at,
                        error_detail=outcome.error_detail,
                    )
                    break

                if outcome.target is None:
                    await mark_failure(
                        sess,
                        job_id=job.job_id,
                        owner=owner,
                        now=now(),
                        error_code=MISSING_HANDLER_ERROR,
                        retryable=False,
                        error_detail="handler returned no target",
                    )
                    break

                if outcome.artifact is not None:
                    job = await commit_artifact_and_advance(
                        sess,
                        job_id=job.job_id,
                        owner=owner,
                        to_status=outcome.target,
                        now=now(),
                        lease_seconds=lease_seconds,
                        progress=outcome.progress,
                        stage=outcome.artifact.stage,
                        artifact_type=outcome.artifact.artifact_type,
                        input_fingerprint=outcome.artifact.input_fingerprint,
                        schema_version=outcome.artifact.schema_version,
                        storage_kind=outcome.artifact.storage_kind,
                        payload_json=outcome.artifact.payload_json,
                        relative_path=outcome.artifact.relative_path,
                        content_hash=outcome.artifact.content_hash,
                        byte_size=outcome.artifact.byte_size,
                    )
                else:
                    job = await advance_job(
                        sess,
                        job_id=job.job_id,
                        owner=owner,
                        to_status=outcome.target,
                        now=now(),
                        lease_seconds=lease_seconds,
                        progress=outcome.progress,
                    )
                heartbeat_stop = asyncio.Event()
                heartbeat = asyncio.create_task(
                    _heartbeat_lease(
                        session_factory,
                        job_id=job_id,
                        owner=owner,
                        lease_seconds=lease_seconds,
                        now=now,
                        stop=heartbeat_stop,
                    )
                )
        finally:
            heartbeat_stop.set()
            await heartbeat

        return await get_job(sess, job_id=job.job_id)


async def _heartbeat_lease(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: str,
    owner: str,
    lease_seconds: float,
    now: Callable[[], float],
    stop: asyncio.Event,
) -> None:
    """Renew a job lease in a separate session while a handler is running."""
    interval = lease_seconds / 3
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        async with session_factory() as heartbeat_session:
            renewed = await renew_lease(
                heartbeat_session,
                job_id=job_id,
                owner=owner,
                now=now(),
                lease_seconds=lease_seconds,
            )
        if not renewed:
            return


async def run_worker_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: float,
    handlers: Mapping[JobStatus, StageHandler],
    now_fn: Callable[[], float] | None = None,
    idle_sleep: float = 1.0,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Run the worker until ``should_stop`` returns True; sleep briefly when idle."""
    while True:
        if should_stop is not None and should_stop():
            break
        try:
            job = await run_worker_once(
                session_factory,
                owner=owner,
                lease_seconds=lease_seconds,
                now_fn=now_fn,
                handlers=handlers,
            )
        except LeaseLostError:
            # A lease lost mid-flight is a normal take-over, not a crash.
            continue
        if job is None:
            await asyncio.sleep(idle_sleep)
