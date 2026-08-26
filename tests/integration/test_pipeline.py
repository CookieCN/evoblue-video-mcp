"""Worker pipeline: submit, fetch metadata/subtitles, register artifacts."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.application.submit import submit_video
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.platforms.base import AdapterError
from evoblue_video_mcp.platforms.models import (
    Transcript,
    TranscriptSegment,
    VideoMetadata,
    VideoRef,
)
from evoblue_video_mcp.runtime.handlers import (
    FetchingMetadataHandler,
    FetchingSubtitlesHandler,
)
from evoblue_video_mcp.runtime.worker import run_worker_once
from evoblue_video_mcp.storage.artifact_store import ArtifactStore
from evoblue_video_mcp.storage.models import JobArtifact


class _FakeAdapter:
    def supports(self, ref: VideoRef) -> bool:
        return True

    async def fetch_metadata(self, ref: VideoRef) -> VideoMetadata:
        return VideoMetadata(
            video_id=ref.video_id, platform=ref.platform, title="Test", author="Author"
        )

    async def fetch_transcript(self, ref: VideoRef) -> Transcript:
        return Transcript(
            segments=[TranscriptSegment(start=0.0, end=2.0, text="hello")],
            language="en",
            source=f"{ref.platform.value}-subtitle",
        )


class _FailingAdapter:
    def supports(self, ref: VideoRef) -> bool:
        return True

    async def fetch_metadata(self, ref: VideoRef) -> VideoMetadata:
        raise AdapterError("METADATA_FETCH_FAILED", "boom", retryable=False)

    async def fetch_transcript(self, ref: VideoRef) -> Transcript:
        raise AdapterError("SUBTITLE_UNAVAILABLE", "no subs", retryable=False)


async def test_metadata_and_subtitle_artifacts_are_registered(
    tmp_path,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    adapter = _FakeAdapter()
    store = ArtifactStore(tmp_path / "artifacts")
    handlers = {
        JobStatus.FETCHING_METADATA: FetchingMetadataHandler(adapter),
        JobStatus.FETCHING_SUBTITLES: FetchingSubtitlesHandler(adapter, store),
    }

    async with session_factory() as sess:
        submitted, reused = await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            config_fingerprint="cfg",
            now=1000.0,
            reuse_window_seconds=3600.0,
        )
        assert reused is False
        job_id = submitted.job_id

    job = await run_worker_once(
        session_factory, owner="w1", lease_seconds=30.0, now=1000.0, handlers=handlers
    )
    assert job is not None

    async with session_factory() as sess:
        artifacts = (
            await sess.scalars(select(JobArtifact).where(JobArtifact.job_id == job_id))
        ).all()
        assert {a.artifact_type for a in artifacts} == {
            "video_metadata",
            "platform_transcript",
        }
        transcript = next(a for a in artifacts if a.artifact_type == "platform_transcript")
        assert transcript.storage_kind == "file"
        assert transcript.relative_path is not None
        assert (tmp_path / "artifacts" / transcript.relative_path).exists()


async def test_failure_maps_error_detail(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    handlers = {JobStatus.FETCHING_METADATA: FetchingMetadataHandler(_FailingAdapter())}

    async with session_factory() as sess:
        await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            config_fingerprint="cfg",
            now=1000.0,
            reuse_window_seconds=3600.0,
        )

    job = await run_worker_once(
        session_factory, owner="w1", lease_seconds=30.0, now=1000.0, handlers=handlers
    )
    assert job is not None
    assert job.status == JobStatus.FAILED.value
    assert job.error_code == "METADATA_FETCH_FAILED"
    assert job.error_detail == "boom"
