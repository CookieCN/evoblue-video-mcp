"""ASR fallback pipeline: SUBTITLE_MISSING -> audio download -> ASR -> report."""

import json
import wave

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.application.submit import submit_video
from evoblue_video_mcp.asr.base import ASRSegment
from evoblue_video_mcp.asr.fake import FakeASRProvider
from evoblue_video_mcp.asr.registry import clear, register_provider
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.platforms.base import SUBTITLE_MISSING, AdapterError
from evoblue_video_mcp.platforms.models import Transcript, VideoMetadata, VideoRef
from evoblue_video_mcp.reports.writer import ReportWriter
from evoblue_video_mcp.runtime.handlers import (
    ChunkingHandler,
    CleaningTranscriptHandler,
    DownloadingAudioHandler,
    FetchingMetadataHandler,
    FetchingSubtitlesHandler,
    GeneratingReportHandler,
    IndexingHandler,
    SummarizingChunksHandler,
    TranscribingHandler,
)
from evoblue_video_mcp.runtime.worker import run_worker_once
from evoblue_video_mcp.storage.artifact_store import ArtifactStore
from evoblue_video_mcp.storage.models import JobArtifact, ModelDownload
from evoblue_video_mcp.storage.repository import get_job, resume_waiting_jobs


def _now() -> float:
    return 1000.0


class _SubtitleMissingAdapter:
    def supports(self, ref: VideoRef) -> bool:
        return True

    async def fetch_metadata(self, ref: VideoRef) -> VideoMetadata:
        return VideoMetadata(video_id=ref.video_id, platform=ref.platform, title="T", author="A")

    async def fetch_transcript(self, ref: VideoRef) -> Transcript:
        raise AdapterError(SUBTITLE_MISSING, "no subs", retryable=False)

    async def download_audio(self, ref: VideoRef, dest_path: str) -> None:
        with wave.open(dest_path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 16000)


class _PipelineLLM:
    async def complete(self, prompt: str) -> str:
        if "JSON" in prompt:
            return json.dumps(
                {
                    "core_summary": "synthesized",
                    "key_takeaways": ["takeaway"],
                    "timeline_outline": "outline",
                    "content_analysis": "analysis",
                }
            )
        return "chunk summary"


async def test_asr_fallback_generates_report(
    tmp_path,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clear()
    register_provider(
        FakeASRProvider(
            segments=[ASRSegment(start=0.0, end=1.0, text="你好世界")],
            detected_language="zh",
        )
    )

    adapter = _SubtitleMissingAdapter()
    store = ArtifactStore(tmp_path / "artifacts")
    writer = ReportWriter(tmp_path / "reports")
    llm = _PipelineLLM()

    handlers = {
        JobStatus.FETCHING_METADATA: FetchingMetadataHandler(adapter),
        JobStatus.FETCHING_SUBTITLES: FetchingSubtitlesHandler(adapter, store),
        JobStatus.DOWNLOADING_AUDIO: DownloadingAudioHandler(adapter, store),
        JobStatus.TRANSCRIBING: TranscribingHandler(store, "fake"),
        JobStatus.CLEANING_TRANSCRIPT: CleaningTranscriptHandler(store),
        JobStatus.CHUNKING: ChunkingHandler(store),
        JobStatus.SUMMARIZING_CHUNKS: SummarizingChunksHandler(store, llm),
        JobStatus.GENERATING_REPORT: GeneratingReportHandler(store, writer, llm),
        JobStatus.INDEXING: IndexingHandler(writer),
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
        session_factory, owner="w1", lease_seconds=30.0, now_fn=_now, handlers=handlers
    )
    assert job is not None
    assert job.status == JobStatus.COMPLETED.value

    async with session_factory() as sess:
        artifacts = (
            await sess.scalars(select(JobArtifact).where(JobArtifact.job_id == job_id))
        ).all()
        types = {a.artifact_type for a in artifacts}
        assert {"audio", "asr_segment", "platform_transcript"} <= types

    reports = list((tmp_path / "reports").glob("*.md"))
    assert len(reports) == 1
    markdown = reports[0].read_text(encoding="utf-8")
    assert 'asr_provider: "fake"' in markdown
    assert 'asr_model: "fake-model"' in markdown


async def test_missing_model_waits_with_explicit_standard_recommendation(
    tmp_path,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clear()
    stale_provider = FakeASRProvider(
        model_id="sensevoice-small-int8",
        model_version="2024-07-17",
        languages=frozenset({"zh", "en", "ja", "ko", "yue"}),
    )
    stale_provider.provider_id = "sherpa-onnx-standard"
    register_provider(stale_provider)
    adapter = _SubtitleMissingAdapter()
    store = ArtifactStore(tmp_path / "artifacts")
    handlers = {
        JobStatus.FETCHING_METADATA: FetchingMetadataHandler(adapter),
        JobStatus.FETCHING_SUBTITLES: FetchingSubtitlesHandler(adapter, store),
        JobStatus.DOWNLOADING_AUDIO: DownloadingAudioHandler(adapter, store),
        JobStatus.TRANSCRIBING: TranscribingHandler(store, "auto"),
    }
    async with session_factory() as sess:
        submitted, _ = await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            language="zh-CN",
            config_fingerprint="cfg",
            now=1000.0,
            reuse_window_seconds=3600.0,
        )

    job = await run_worker_once(
        session_factory, owner="w1", lease_seconds=30.0, now_fn=_now, handlers=handlers
    )
    assert job is not None
    assert job.status == JobStatus.WAITING_FOR_MODEL.value
    assert job.asr_provider_id == "sherpa-onnx-standard"
    assert job.asr_recommendation_model_id == "sensevoice-small-int8"
    assert job.lease_owner is None

    async with session_factory() as sess:
        assert (await sess.scalars(select(ModelDownload))).all() == []
        resumed = await resume_waiting_jobs(
            sess, model_id="sensevoice-small-int8", now=1001.0
        )
        assert resumed == [submitted.job_id]
        stored = await get_job(sess, job_id=submitted.job_id)
        assert stored is not None
        assert stored.status == JobStatus.TRANSCRIBING.value


async def test_asr_disabled_fails_without_downloading(
    tmp_path,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clear()
    adapter = _SubtitleMissingAdapter()
    store = ArtifactStore(tmp_path / "artifacts")
    handlers = {
        JobStatus.FETCHING_METADATA: FetchingMetadataHandler(adapter),
        JobStatus.FETCHING_SUBTITLES: FetchingSubtitlesHandler(adapter, store),
    }

    async with session_factory() as sess:
        submitted, _ = await submit_video(
            sess,
            url="https://youtu.be/dQw4w9WgXcQ",
            asr="disabled",
            config_fingerprint="cfg",
            now=1000.0,
            reuse_window_seconds=3600.0,
        )
        job_id = submitted.job_id

    job = await run_worker_once(
        session_factory, owner="w1", lease_seconds=30.0, now_fn=_now, handlers=handlers
    )
    assert job is not None
    assert job.status == JobStatus.FAILED.value
    assert job.error_code == "ASR_DISABLED"

    async with session_factory() as sess:
        artifacts = (
            await sess.scalars(select(JobArtifact).where(JobArtifact.job_id == job_id))
        ).all()
        assert "audio" not in {a.artifact_type for a in artifacts}
