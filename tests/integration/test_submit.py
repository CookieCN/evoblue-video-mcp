"""Submit deduplicates on canonical URL within a reuse window (no network)."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.application.submit import compute_request_fingerprint, submit_video


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


async def test_submit_creates_new_job_after_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        first, _ = await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            config_fingerprint="cfg",
            now=1000.0,
            reuse_window_seconds=100.0,
        )
        second, reused = await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            config_fingerprint="cfg",
            now=1200.0,
            reuse_window_seconds=100.0,
        )
        assert reused is False
        assert second.job_id != first.job_id
