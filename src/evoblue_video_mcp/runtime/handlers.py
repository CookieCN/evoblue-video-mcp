"""Pipeline stage handlers from metadata acquisition through report generation."""

import errno
import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.llm.base import LLMError, LLMProvider
from evoblue_video_mcp.platforms.base import AdapterError, PlatformAdapter
from evoblue_video_mcp.platforms.detector import PlatformError, detect_video
from evoblue_video_mcp.platforms.models import Transcript, TranscriptSegment, VideoMetadata
from evoblue_video_mcp.reports.renderer import render_markdown
from evoblue_video_mcp.reports.schema import ReportDocument
from evoblue_video_mcp.reports.writer import ReportWriter, safe_filename
from evoblue_video_mcp.runtime.worker import ArtifactRecord, StageOutcome
from evoblue_video_mcp.storage.artifact_store import ArtifactIntegrityError, ArtifactStore
from evoblue_video_mcp.storage.models import Job
from evoblue_video_mcp.storage.repository import get_artifact
from evoblue_video_mcp.transcript.chunker import chunk_transcript
from evoblue_video_mcp.transcript.cleaner import clean_transcript

METADATA_ARTIFACT_TYPE = "video_metadata"
TRANSCRIPT_ARTIFACT_TYPE = "platform_transcript"
CLEANED_ARTIFACT_TYPE = "cleaned_transcript"
CHUNKS_ARTIFACT_TYPE = "chunks"
SUMMARIES_ARTIFACT_TYPE = "chunk_summaries"
REPORT_ARTIFACT_TYPE = "report"


def _stage_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _adapter_outcome(exc: AdapterError) -> StageOutcome:
    if exc.retryable:
        return StageOutcome.transient(exc.error_code, error_detail=str(exc))
    return StageOutcome.fatal(exc.error_code, error_detail=str(exc))


def _llm_outcome(exc: LLMError) -> StageOutcome:
    if exc.retryable:
        return StageOutcome.transient(exc.error_code, error_detail=str(exc))
    return StageOutcome.fatal(exc.error_code, error_detail=str(exc))


def metadata_to_json(metadata: VideoMetadata) -> str:
    return json.dumps(
        {
            "video_id": metadata.video_id,
            "platform": metadata.platform.value,
            "title": metadata.title,
            "author": metadata.author,
            "duration": metadata.duration,
            "published_at": metadata.published_at,
        },
        ensure_ascii=False,
    )


def transcript_to_json(transcript: Transcript) -> str:
    return json.dumps(
        {
            "language": transcript.language,
            "source": transcript.source,
            "segments": [
                {"start": seg.start, "end": seg.end, "text": seg.text}
                for seg in transcript.segments
            ],
        },
        ensure_ascii=False,
    )


def transcript_from_json(raw: str) -> Transcript:
    data = json.loads(raw)
    return Transcript(
        language=data.get("language", ""),
        source=data.get("source", ""),
        segments=[
            TranscriptSegment(start=s["start"], end=s["end"], text=s["text"])
            for s in data.get("segments", [])
        ],
    )


async def _load_json_artifact(
    session: AsyncSession, store: ArtifactStore, job_id: str, artifact_type: str, fingerprint: str
) -> str | None:
    artifact = await get_artifact(
        session, job_id=job_id, artifact_type=artifact_type, input_fingerprint=fingerprint
    )
    if artifact is None:
        return None
    if artifact.storage_kind == "inline_json":
        return artifact.payload_json
    if artifact.relative_path is not None and artifact.content_hash is not None:
        content = await store.read_file_verified(artifact.relative_path, artifact.content_hash)
        return content.decode("utf-8")
    return None


class FetchingMetadataHandler:
    """Fetch video metadata and register it as an inline JSON artifact."""

    def __init__(self, adapter: PlatformAdapter) -> None:
        self._adapter = adapter

    async def execute(self, job: Job, session: AsyncSession) -> StageOutcome:
        try:
            ref = detect_video(job.url)
            metadata = await self._adapter.fetch_metadata(ref)
        except PlatformError as exc:
            return StageOutcome.fatal(exc.error_code, error_detail=str(exc))
        except AdapterError as exc:
            return _adapter_outcome(exc)

        return StageOutcome.success(
            target=JobStatus.FETCHING_SUBTITLES,
            artifact=ArtifactRecord(
                stage="fetching_metadata",
                artifact_type=METADATA_ARTIFACT_TYPE,
                input_fingerprint=_stage_fingerprint(job.url),
                schema_version=1,
                storage_kind="inline_json",
                payload_json=metadata_to_json(metadata),
            ),
        )


class FetchingSubtitlesHandler:
    """Fetch a platform transcript and store it as a file artifact."""

    def __init__(self, adapter: PlatformAdapter, store: ArtifactStore) -> None:
        self._adapter = adapter
        self._store = store

    async def execute(self, job: Job, session: AsyncSession) -> StageOutcome:
        try:
            ref = detect_video(job.url)
            transcript = await self._adapter.fetch_transcript(ref)
        except PlatformError as exc:
            return StageOutcome.fatal(exc.error_code, error_detail=str(exc))
        except AdapterError as exc:
            return _adapter_outcome(exc)

        content = transcript_to_json(transcript).encode("utf-8")
        content_hash = hashlib.sha256(content).hexdigest()
        relative_path = f"{job.job_id}/{TRANSCRIPT_ARTIFACT_TYPE}_{content_hash}.json"
        try:
            stored_hash, byte_size = await self._store.write_file(relative_path, content)
        except OSError as exc:
            code = "DISK_SPACE_LOW" if exc.errno == errno.ENOSPC else "INTERNAL_ERROR"
            return StageOutcome.fatal(code, error_detail=str(exc))

        return StageOutcome.success(
            target=JobStatus.CLEANING_TRANSCRIPT,
            artifact=ArtifactRecord(
                stage="fetching_subtitles",
                artifact_type=TRANSCRIPT_ARTIFACT_TYPE,
                input_fingerprint=_stage_fingerprint(job.url),
                schema_version=1,
                storage_kind="file",
                relative_path=relative_path,
                content_hash=stored_hash,
                byte_size=byte_size,
            ),
        )


class CleaningTranscriptHandler:
    """Clean the fetched transcript and store the result inline."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    async def execute(self, job: Job, session: AsyncSession) -> StageOutcome:
        fingerprint = _stage_fingerprint(job.url)
        raw = await _load_json_artifact(
            session, self._store, job.job_id, TRANSCRIPT_ARTIFACT_TYPE, fingerprint
        )
        if raw is None:
            return StageOutcome.fatal("INTERNAL_ERROR", error_detail="missing transcript artifact")

        cleaned = clean_transcript(transcript_from_json(raw))
        return StageOutcome.success(
            target=JobStatus.CHUNKING,
            artifact=ArtifactRecord(
                stage="cleaning_transcript",
                artifact_type=CLEANED_ARTIFACT_TYPE,
                input_fingerprint=fingerprint,
                schema_version=1,
                storage_kind="inline_json",
                payload_json=transcript_to_json(cleaned),
            ),
        )


class ChunkingHandler:
    """Split the cleaned transcript into bounded chunks."""

    def __init__(self, store: ArtifactStore, max_chars: int = 4000) -> None:
        self._store = store
        self._max_chars = max_chars

    async def execute(self, job: Job, session: AsyncSession) -> StageOutcome:
        fingerprint = _stage_fingerprint(job.url)
        raw = await _load_json_artifact(
            session, self._store, job.job_id, CLEANED_ARTIFACT_TYPE, fingerprint
        )
        if raw is None:
            return StageOutcome.fatal("INTERNAL_ERROR", error_detail="missing cleaned transcript")

        chunks = chunk_transcript(transcript_from_json(raw), self._max_chars)
        return StageOutcome.success(
            target=JobStatus.SUMMARIZING_CHUNKS,
            artifact=ArtifactRecord(
                stage="chunking",
                artifact_type=CHUNKS_ARTIFACT_TYPE,
                input_fingerprint=fingerprint,
                schema_version=1,
                storage_kind="inline_json",
                payload_json=json.dumps(chunks, ensure_ascii=False),
            ),
        )


class SummarizingChunksHandler:
    """Summarize each chunk with the LLM provider."""

    def __init__(self, store: ArtifactStore, llm: LLMProvider) -> None:
        self._store = store
        self._llm = llm

    async def execute(self, job: Job, session: AsyncSession) -> StageOutcome:
        fingerprint = _stage_fingerprint(job.url)
        raw = await _load_json_artifact(
            session, self._store, job.job_id, CHUNKS_ARTIFACT_TYPE, fingerprint
        )
        if raw is None:
            return StageOutcome.fatal("INTERNAL_ERROR", error_detail="missing chunks artifact")

        chunks = json.loads(raw)
        summaries: list[str] = []
        try:
            for chunk in chunks:
                summaries.append(await self._llm.complete(_summary_prompt(chunk)))
        except LLMError as exc:
            return _llm_outcome(exc)

        return StageOutcome.success(
            target=JobStatus.GENERATING_REPORT,
            artifact=ArtifactRecord(
                stage="summarizing_chunks",
                artifact_type=SUMMARIES_ARTIFACT_TYPE,
                input_fingerprint=fingerprint,
                schema_version=1,
                storage_kind="inline_json",
                payload_json=json.dumps(summaries, ensure_ascii=False),
            ),
        )


class GeneratingReportHandler:
    """Render and atomically write the Markdown report."""

    def __init__(self, store: ArtifactStore, writer: ReportWriter) -> None:
        self._store = store
        self._writer = writer

    async def execute(self, job: Job, session: AsyncSession) -> StageOutcome:
        fingerprint = _stage_fingerprint(job.url)
        metadata_raw = await _load_json_artifact(
            session, self._store, job.job_id, METADATA_ARTIFACT_TYPE, fingerprint
        )
        summaries_raw = await _load_json_artifact(
            session, self._store, job.job_id, SUMMARIES_ARTIFACT_TYPE, fingerprint
        )
        if metadata_raw is None or summaries_raw is None:
            return StageOutcome.fatal(
                "INTERNAL_ERROR", error_detail="missing metadata or summaries artifact"
            )

        metadata = json.loads(metadata_raw)
        summaries = json.loads(summaries_raw)
        doc = ReportDocument(
            analysis_id=job.job_id,
            source_url=cast(HttpUrl, job.url),
            platform=metadata["platform"],
            video_id=metadata["video_id"],
            title=metadata["title"],
            author=metadata.get("author", ""),
            analyzed_at=_now(),
            summary_mode=cast(Literal["auto", "standard", "unboxing"], job.mode),
            core_summary="\n\n".join(summaries),
        )
        markdown = render_markdown(doc)
        filename = safe_filename(doc.title, job.job_id)
        result = await self._writer.write_report(filename, markdown)
        content = markdown.encode("utf-8")

        return StageOutcome.success(
            target=JobStatus.INDEXING,
            artifact=ArtifactRecord(
                stage="generating_report",
                artifact_type=REPORT_ARTIFACT_TYPE,
                input_fingerprint=fingerprint,
                schema_version=1,
                storage_kind="file",
                relative_path=result.path,
                content_hash=result.content_hash,
                byte_size=len(content),
            ),
        )


class IndexingHandler:
    """Verify the generated report and complete the job (FTS indexing arrives in P3).

    P2's ``completed`` means a Markdown report was generated and its file is
    intact. FTS5 indexing is a P3 concern; this stage does not pretend to index.
    """

    def __init__(self, writer: ReportWriter) -> None:
        self._writer = writer

    async def execute(self, job: Job, session: AsyncSession) -> StageOutcome:
        fingerprint = _stage_fingerprint(job.url)
        artifact = await get_artifact(
            session,
            job_id=job.job_id,
            artifact_type=REPORT_ARTIFACT_TYPE,
            input_fingerprint=fingerprint,
        )
        if artifact is None or artifact.relative_path is None or artifact.content_hash is None:
            return StageOutcome.fatal("INTERNAL_ERROR", error_detail="missing report artifact")
        try:
            await self._writer.read_file_verified(artifact.relative_path, artifact.content_hash)
        except ArtifactIntegrityError as exc:
            return StageOutcome.fatal("INDEX_FAILED", error_detail=str(exc))
        return StageOutcome.success(target=JobStatus.COMPLETED)


def _summary_prompt(chunk: str) -> str:
    return f"Summarize the following transcript chunk concisely:\n\n{chunk}"


def _now() -> datetime:
    return datetime.now(UTC)
