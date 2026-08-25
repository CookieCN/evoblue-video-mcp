"""Submit a video URL for analysis, reusing a recent matching job when applicable."""

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evoblue_video_mcp.platforms.detector import detect_video
from evoblue_video_mcp.storage.models import Job
from evoblue_video_mcp.storage.repository import enqueue_job


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


def new_job_id() -> str:
    return uuid.uuid4().hex


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
    """Submit a URL; returns ``(job, reused)`` where ``reused`` marks a recent match.

    The canonical URL, not the raw input, feeds the fingerprint, so different
    share links for the same video dedupe to one job.
    """
    ref = detect_video(url)
    fingerprint = compute_request_fingerprint(
        canonical_url=ref.url,
        mode=mode,
        asr=asr,
        language=language,
        config_fingerprint=config_fingerprint,
    )

    cutoff = now - reuse_window_seconds
    existing = (
        await session.scalars(
            select(Job)
            .where(Job.request_fingerprint == fingerprint, Job.created_at >= cutoff)
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).first()
    if existing is not None:
        return existing, True

    job = await enqueue_job(
        session,
        job_id=new_job_id(),
        url=ref.url,
        request_fingerprint=fingerprint,
        config_fingerprint=config_fingerprint,
        now=now,
        mode=mode,
        asr=asr,
        language=language,
    )
    return job, False
