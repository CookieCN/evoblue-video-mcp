"""Submit a video URL for analysis, reusing a recent matching job when applicable."""

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evoblue_video_mcp.jobs import TERMINAL_JOB_STATUSES, JobStatus
from evoblue_video_mcp.platforms.detector import detect_video
from evoblue_video_mcp.storage.models import Job
from evoblue_video_mcp.storage.repository import get_job

_TERMINAL_VALUES = [state.value for state in TERMINAL_JOB_STATUSES]


def compute_request_fingerprint(
    *,
    canonical_url: str,
    mode: str,
    asr: str,
    language: str | None,
    config_fingerprint: str,
) -> str:
    """Derive a stable fingerprint from the canonical URL and analysis options."""
    raw = "|".join([canonical_url, mode, asr, language or "", config_fingerprint])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_config_fingerprint(
    *, provider: str, model: str, base_url: str = "", asr_provider: str = "auto"
) -> str:
    """Derive a fingerprint from LLM configuration that affects report output."""
    material = (
        f"pipeline-v1|report-v1|chunks-4000|{provider}|{base_url}|{model}|{asr_provider}"
    )
    return hashlib.sha256(material.encode()).hexdigest()


def new_job_id() -> str:
    return uuid.uuid4().hex


async def _find_reusable(
    session: AsyncSession,
    fingerprint: str,
    now: float,
    reuse_window_seconds: float,
) -> Job | None:
    # Any non-terminal job for this fingerprint is reused regardless of age.
    active = (
        await session.scalars(
            select(Job)
            .where(Job.request_fingerprint == fingerprint, Job.status.not_in(_TERMINAL_VALUES))
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).first()
    if active is not None:
        return active

    # A recently completed job is reused; failed/cancelled are never reused.
    cutoff = now - reuse_window_seconds
    return (
        await session.scalars(
            select(Job)
            .where(
                Job.request_fingerprint == fingerprint,
                Job.status == JobStatus.COMPLETED.value,
                Job.updated_at >= cutoff,
            )
            .order_by(Job.updated_at.desc())
            .limit(1)
        )
    ).first()


async def submit_video(
    session: AsyncSession,
    *,
    url: str,
    mode: str = "auto",
    asr: str = "auto",
    language: str | None = None,
    config_fingerprint: str,
    now: float,
    reuse_window_seconds: float,
) -> tuple[Job, bool]:
    """Submit a URL; returns ``(job, reused)`` where ``reused`` marks a reuse.

    The lookup and insert run under a SQLite IMMEDIATE transaction so two
    concurrent submissions of the same URL cannot each create a job.
    """
    ref = detect_video(url)
    fingerprint = compute_request_fingerprint(
        canonical_url=ref.url,
        mode=mode,
        asr=asr,
        language=language,
        config_fingerprint=config_fingerprint,
    )

    await session.rollback()
    conn = await session.connection()
    await conn.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        existing = await _find_reusable(session, fingerprint, now, reuse_window_seconds)
        if existing is not None:
            await session.commit()
            return existing, True

        job = Job(
            job_id=new_job_id(),
            request_fingerprint=fingerprint,
            url=ref.url,
            mode=mode,
            asr=asr,
            language=language,
            config_fingerprint=config_fingerprint,
            status=JobStatus.QUEUED.value,
            max_attempts=3,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        await session.commit()
        created = await get_job(session, job_id=job.job_id)
        if created is None:
            raise RuntimeError(f"job {job.job_id} vanished after submit")
        return created, False
    except Exception:
        await session.rollback()
        raise
