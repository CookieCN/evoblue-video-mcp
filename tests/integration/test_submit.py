"""Submit deduplicates on canonical URL within a reuse window (no network)."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.application.submit import compute_request_fingerprint, submit_video
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.platforms.detector import detect_video
from evoblue_video_mcp.storage.repository import enqueue_job


def test_compute_request_fingerprint_is_deterministic() -> None:
    base = dict(
        canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        mode="auto",
        asr="auto",
        language=None,
        config_fingerprint="cfg",
    )
    assert compute_request_fingerprint(**base) == compute_request_fingerprint(**base)
    different_mode = {**base, "mode": "standard"}
    assert compute_request_fingerprint(**base) != compute_request_fingerprint(**different_mode)


async def test_submit_reuses_recent_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        first, reused_first = await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            config_fingerprint="cfg",
            now=1000.0,
            reuse_window_seconds=3600.0,
        )
        assert reused_first is False

        # A different share form of the same video reuses the job.
        second, reused_second = await submit_video(
            sess,
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            config_fingerprint="cfg",
            now=1100.0,
            reuse_window_seconds=3600.0,
        )
        assert reused_second is True
        assert second.job_id == first.job_id


async def test_completed_job_not_reused_after_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ref = detect_video("https://youtu.be/dQw4w9WgXcQ")
    fingerprint = compute_request_fingerprint(
        canonical_url=ref.url, mode="auto", asr="auto", language=None, config_fingerprint="cfg"
    )
    async with session_factory() as sess:
        await enqueue_job(
            sess,
            job_id="done-1",
            url=ref.url,
            request_fingerprint=fingerprint,
            config_fingerprint="cfg",
            now=1000.0,
            status=JobStatus.COMPLETED,
        )
        job, reused = await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            config_fingerprint="cfg",
            now=2000.0,
            reuse_window_seconds=100.0,
        )
        assert reused is False
        assert job.job_id != "done-1"


async def test_concurrent_submit_creates_one_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def submit() -> object:
        async with session_factory() as sess:
            return await submit_video(
                sess,
                url="https://youtu.be/dQw4w9WgXcQ",
                config_fingerprint="cfg",
                now=1000.0,
                reuse_window_seconds=3600.0,
            )

    results = await asyncio.gather(submit(), submit())
    job_ids = {job.job_id for job, _ in results}
    assert len(job_ids) == 1


async def test_active_job_reused_regardless_of_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        first, _ = await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            config_fingerprint="cfg",
            now=1000.0,
            reuse_window_seconds=1.0,
        )
        # The queued job is active, so it reuses even far outside the window.
        second, reused = await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            config_fingerprint="cfg",
            now=5000.0,
            reuse_window_seconds=1.0,
        )
        assert reused is True
        assert second.job_id == first.job_id


async def test_failed_job_is_not_reused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ref = detect_video("https://youtu.be/dQw4w9WgXcQ")
    fingerprint = compute_request_fingerprint(
        canonical_url=ref.url, mode="auto", asr="auto", language=None, config_fingerprint="cfg"
    )
    async with session_factory() as sess:
        await enqueue_job(
            sess,
            job_id="failed-job",
            url=ref.url,
            request_fingerprint=fingerprint,
            config_fingerprint="cfg",
            now=1000.0,
            status=JobStatus.FAILED,
        )
        job, reused = await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            config_fingerprint="cfg",
            now=1100.0,
            reuse_window_seconds=3600.0,
        )
        assert reused is False
        assert job.job_id != "failed-job"
