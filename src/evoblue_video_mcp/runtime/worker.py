"""Worker execution loop: claim a job, run its stage handler, advance state.

A worker claims one job and drives it stage-by-stage until it reaches a terminal
state, pauses into ``retry_wait``, or fails. Stage handlers are injected, so the
loop is testable without any real video pipeline.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.jobs import TERMINAL_JOB_STATUSES, JobStatus
from evoblue_video_mcp.storage.models import Job
from evoblue_video_mcp.storage.repository import (
    advance_job,
    claim_next_job,
    commit_artifact_and_advance,
    get_job,
    mark_failure,
)

MISSING_HANDLER_ERROR = "INTERNAL_ERROR"
DEFAULT_RETRY_DELAY = 60.0


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


class StageHandler(Protocol):
    async def execute(self, job: Job) -> StageOutcome: ...


async def run_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: float,
    now: float,
    handlers: Mapping[JobStatus, StageHandler],
) -> Job | None:
    """Claim one job and drive it through its stages; returns the job or ``None`` if idle."""
    async with session_factory() as sess:
        job = await claim_next_job(sess, owner=owner, lease_seconds=lease_seconds, now=now)
        if job is None:
            return None

        while True:
            status = JobStatus(job.status)
            if status in TERMINAL_JOB_STATUSES:
                break

            handler = handlers.get(status)
            if handler is None:
                await mark_failure(
                    sess,
                    job_id=job.job_id,
                    owner=owner,
                    now=now,
                    error_code=MISSING_HANDLER_ERROR,
                    retryable=False,
                    error_detail=f"no handler for stage {status.value}",
                )
                break

            outcome = await handler.execute(job)
            if outcome.error_code is not None:
                next_retry_at = outcome.next_retry_at
                if next_retry_at is None and outcome.retryable:
                    next_retry_at = now + DEFAULT_RETRY_DELAY
                await mark_failure(
                    sess,
                    job_id=job.job_id,
                    owner=owner,
                    now=now,
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
                    now=now,
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
                    now=now,
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
                    now=now,
                    lease_seconds=lease_seconds,
                    progress=outcome.progress,
                )

        return await get_job(sess, job_id=job.job_id)
