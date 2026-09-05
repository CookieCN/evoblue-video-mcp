"""Submit deduplicates on canonical URL within a reuse window (no network)."""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError as SQLOperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import evoblue_video_mcp.application.submit as submit_module
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


async def test_immediate_lock_timeout_leaves_session_reusable(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3-005 review P1-2: a timed-out BEGIN IMMEDIATE must not desync the
    session. The lock holder keeps the write lock; submit fails on the busy
    timeout; the SAME session then begins/commits its next transaction."""
    # Hold the write lock from a separate driver connection (this test owns it).
    lock = sqlite3.connect(str(tmp_path / "test.db"), timeout=30.0)
    lock.execute("BEGIN IMMEDIATE")
    lock.execute(
        "INSERT INTO app_settings (id, setup_completed, updated_at) VALUES (99, 1, 1.0)"
    )

    lookup_calls: list[int] = []
    original_lookup = submit_module._find_reusable

    async def _spy_lookup(*args: object, **kwargs: object) -> object:
        lookup_calls.append(1)
        return await original_lookup(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(submit_module, "_find_reusable", _spy_lookup)

    async with session_factory() as sess:
        # The driver error surfaces either raw or SQLAlchemy-wrapped depending
        # on where the busy timeout fires; catch both shapes.
        with pytest.raises(
            (sqlite3.OperationalError, SQLOperationalError), match="database is locked"
        ):
            await submit_video(
                sess,
                url="https://youtu.be/dQw4w9WgXcQ",
                config_fingerprint="cfg",
                now=1000.0,
                reuse_window_seconds=3600.0,
            )
        # BEGIN IMMEDIATE failed BEFORE the lookup ran — proof the execution
        # option reached the begin hook. A deferred BEGIN would have succeeded
        # under the held lock and the failure would have happened later (after
        # the lookup) at the INSERT.
        assert lookup_calls == []

        # Release the lock, then the SAME session must begin/commit cleanly.
        lock.rollback()
        lock.close()

        job, reused = await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            config_fingerprint="cfg",
            now=1000.5,
            reuse_window_seconds=3600.0,
        )
        assert reused is False and job.status == JobStatus.QUEUED.value
        assert len(lookup_calls) == 1  # the retried submit passed the lookup

    # The failed submit must not have left a half-created job behind.
    async with session_factory() as sess:
        total = (await sess.execute(text("SELECT count(*) FROM jobs"))).scalar()
        assert total == 1

    # The failed submit must not have left a half-created job behind.
    async with session_factory() as sess:
        total = (await sess.execute(text("SELECT count(*) FROM jobs"))).scalar()
        assert total == 1
