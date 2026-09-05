"""Pipeline stage handlers from metadata acquisition through report generation."""

import errno
import hashlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from evoblue_video_mcp.asr.approvals import is_formal_default
from evoblue_video_mcp.asr.base import ASRError, ASRRequest, ASRSegment, segment_key
from evoblue_video_mcp.asr.registry import get_provider, list_providers
from evoblue_video_mcp.asr.routing import ProviderOption, route_asr
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.llm.base import LLMError, LLMProvider
from evoblue_video_mcp.platforms.base import SUBTITLE_MISSING, AdapterError, PlatformAdapter
from evoblue_video_mcp.platforms.detector import PlatformError, detect_video
from evoblue_video_mcp.platforms.models import Transcript, TranscriptSegment, VideoMetadata
from evoblue_video_mcp.reports.parser import MarkdownParseError, decode_report_bytes
from evoblue_video_mcp.reports.renderer import render_markdown
from evoblue_video_mcp.reports.schema import ReportDocument
from evoblue_video_mcp.reports.synthesis import SynthesisError, synthesize
from evoblue_video_mcp.reports.writer import ReportConflictError, ReportWriter, report_filename
from evoblue_video_mcp.runtime.worker import ArtifactRecord, StageContext, StageOutcome
from evoblue_video_mcp.storage.artifact_store import ArtifactIntegrityError, ArtifactStore
from evoblue_video_mcp.storage.db import immediate_write_transaction
from evoblue_video_mcp.storage.models import Job
from evoblue_video_mcp.storage.report_repository import (
    FtsBodyTexts,
    ReportIndexEntry,
    upsert_report_document,
)
from evoblue_video_mcp.storage.repository import (
    get_artifact,
    get_installations,
    pin_job_asr_route,
    save_artifact_inline,
)
from evoblue_video_mcp.transcript.chunker import chunk_transcript
from evoblue_video_mcp.transcript.cleaner import clean_transcript

METADATA_ARTIFACT_TYPE = "video_metadata"
TRANSCRIPT_ARTIFACT_TYPE = "platform_transcript"
CLEANED_ARTIFACT_TYPE = "cleaned_transcript"
CHUNKS_ARTIFACT_TYPE = "chunks"
SUMMARIES_ARTIFACT_TYPE = "chunk_summaries"
CHUNK_SUMMARY_ARTIFACT_TYPE = "chunk_summary"
REPORT_ARTIFACT_TYPE = "report"
AUDIO_ARTIFACT_TYPE = "audio"
ASR_SEGMENT_ARTIFACT_TYPE = "asr_segment"


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


def _asr_outcome(exc: ASRError) -> StageOutcome:
    if exc.error_code == "ASR_CANCELLED":
        return StageOutcome.fatal("CANCELLED_BY_USER", error_detail="cancelled")
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


def _asr_segments_to_json(segments: list[ASRSegment]) -> str:
    return json.dumps(
        [{"start": s.start, "end": s.end, "text": s.text} for s in segments],
        ensure_ascii=False,
    )


def _asr_segments_from_json(raw: str) -> list[ASRSegment]:
    data = json.loads(raw)
    return [
        ASRSegment(start=float(item["start"]), end=float(item["end"]), text=str(item["text"]))
        for item in data
    ]


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

    async def execute(self, job: Job, session: AsyncSession, ctx: StageContext) -> StageOutcome:
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

    async def execute(self, job: Job, session: AsyncSession, ctx: StageContext) -> StageOutcome:
        try:
            ref = detect_video(job.url)
            transcript = await self._adapter.fetch_transcript(ref)
        except PlatformError as exc:
            return StageOutcome.fatal(exc.error_code, error_detail=str(exc))
        except AdapterError as exc:
            if exc.error_code == SUBTITLE_MISSING:
                if job.asr == "disabled":
                    return StageOutcome.fatal(
                        "ASR_DISABLED", error_detail="no subtitles and ASR disabled"
                    )
                return StageOutcome.success(target=JobStatus.DOWNLOADING_AUDIO)
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


class DownloadingAudioHandler:
    """Download the audio track and store it as a file artifact."""

    def __init__(self, adapter: PlatformAdapter, store: ArtifactStore) -> None:
        self._adapter = adapter
        self._store = store

    async def execute(self, job: Job, session: AsyncSession, ctx: StageContext) -> StageOutcome:
        try:
            ref = detect_video(job.url)
        except PlatformError as exc:
            return StageOutcome.fatal(exc.error_code, error_detail=str(exc))

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_audio = os.path.join(tmpdir, "audio.wav")
            try:
                await self._adapter.download_audio(ref, tmp_audio)
            except AdapterError as exc:
                return _adapter_outcome(exc)
            content = Path(tmp_audio).read_bytes()

        content_hash = hashlib.sha256(content).hexdigest()
        relative_path = f"{job.job_id}/audio_{content_hash}.wav"
        try:
            stored_hash, byte_size = await self._store.write_file(relative_path, content)
        except OSError as exc:
            code = "DISK_SPACE_LOW" if exc.errno == errno.ENOSPC else "INTERNAL_ERROR"
            return StageOutcome.fatal(code, error_detail=str(exc))

        return StageOutcome.success(
            target=JobStatus.TRANSCRIBING,
            artifact=ArtifactRecord(
                stage="downloading_audio",
                artifact_type=AUDIO_ARTIFACT_TYPE,
                input_fingerprint=_stage_fingerprint(job.url),
                schema_version=1,
                storage_kind="file",
                relative_path=relative_path,
                content_hash=stored_hash,
                byte_size=byte_size,
            ),
        )


class TranscribingHandler:
    """Transcribe audio via a registered ASR provider with per-segment checkpointing."""

    def __init__(self, store: ArtifactStore, provider_id: str = "auto") -> None:
        self._store = store
        self._provider_id = provider_id

    async def execute(self, job: Job, session: AsyncSession, ctx: StageContext) -> StageOutcome:
        try:
            decision = route_asr(
                job.language,
                job.asr_provider_id or self._provider_id,
                await _provider_options(session),
            )
        except ValueError as exc:
            return StageOutcome.fatal("ASR_LANGUAGE_UNSUPPORTED", error_detail=str(exc))

        # §6: _provider_options above opened a deferred read snapshot and the
        # pin reads then writes — as an IMMEDIATE transaction a concurrent
        # writer committing in between degrades to the busy timeout instead
        # of an unretryable BUSY_SNAPSHOT (which the worker would convert
        # into a spurious INTERNAL_ERROR). The read transaction is closed
        # explicitly first: the write helper refuses an open transaction.
        await session.commit()
        async with immediate_write_transaction(session):
            await pin_job_asr_route(
                session,
                job_id=job.job_id,
                owner=ctx.owner,
                now=ctx.now(),
                provider_id=decision.provider_id,
                model_id=decision.model_id,
                model_version=decision.model_version,
                recommendation_model_id=decision.model_id if decision.recommendation else None,
            )
        if not decision.installed:
            return StageOutcome.success(target=JobStatus.WAITING_FOR_MODEL)

        provider = get_provider(decision.provider_id)
        if provider is None:
            return StageOutcome.success(target=JobStatus.WAITING_FOR_MODEL)

        fingerprint = _stage_fingerprint(job.url)
        audio = await get_artifact(
            session,
            job_id=job.job_id,
            artifact_type=AUDIO_ARTIFACT_TYPE,
            input_fingerprint=fingerprint,
        )
        if audio is None or audio.relative_path is None or audio.content_hash is None:
            return StageOutcome.fatal("INTERNAL_ERROR", error_detail="missing audio artifact")
        audio_content = await self._store.read_file_verified(
            audio.relative_path, audio.content_hash
        )

        checkpoint = await get_artifact(
            session,
            job_id=job.job_id,
            artifact_type=ASR_SEGMENT_ARTIFACT_TYPE,
            input_fingerprint=fingerprint,
        )
        completed = (
            _asr_segments_from_json(checkpoint.payload_json)
            if checkpoint is not None and checkpoint.payload_json is not None
            else []
        )
        resume_keys = frozenset(segment_key(s.start, s.end) for s in completed)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_audio = os.path.join(tmpdir, "audio.wav")
            Path(tmp_audio).write_bytes(audio_content)
            await session.commit()

            new_segments: list[ASRSegment] = []

            async def checkpoint_segment(segment: ASRSegment) -> None:
                new_segments.append(segment)
                await save_artifact_inline(
                    session,
                    job_id=job.job_id,
                    owner=ctx.owner,
                    now=ctx.now(),
                    stage="transcribing",
                    artifact_type=ASR_SEGMENT_ARTIFACT_TYPE,
                    input_fingerprint=fingerprint,
                    schema_version=1,
                    payload_json=_asr_segments_to_json(completed + new_segments),
                )

            try:
                result = await provider.transcribe(
                    ASRRequest(audio_path=tmp_audio, language=job.language),
                    is_cancelled=ctx.is_cancelled,
                    on_segment=checkpoint_segment,
                    resume_keys=resume_keys,
                )
            except ASRError as exc:
                return _asr_outcome(exc)

        all_segments = completed + list(result.segments)
        transcript = Transcript(
            segments=[
                TranscriptSegment(start=s.start, end=s.end, text=s.text) for s in all_segments
            ],
            language=result.detected_language or job.language or "",
            source=f"{result.provider_id}-asr",
        )
        return StageOutcome.success(
            target=JobStatus.CLEANING_TRANSCRIPT,
            artifact=ArtifactRecord(
                stage="transcribing",
                artifact_type=TRANSCRIPT_ARTIFACT_TYPE,
                input_fingerprint=fingerprint,
                schema_version=1,
                storage_kind="inline_json",
                payload_json=transcript_to_json(transcript),
            ),
        )


async def _provider_options(session: AsyncSession) -> tuple[ProviderOption, ...]:
    installed_ids = set(list_providers())
    installed_models = {item.model_id for item in await get_installations(session)}
    options: list[ProviderOption] = [
        ProviderOption(
            provider_id="sherpa-onnx-lite",
            model_id="zipformer-ctc-small-zh-int8",
            model_version="2025-07-16",
            tier="lite",
            languages=frozenset({"zh"}),
            installed=(
                "sherpa-onnx-lite" in installed_ids
                and "zipformer-ctc-small-zh-int8" in installed_models
            ),
            formal_default=is_formal_default("zipformer-ctc-small-zh-int8", "2025-07-16"),
        ),
        ProviderOption(
            provider_id="sherpa-onnx-standard",
            model_id="sensevoice-small-int8",
            model_version="2024-07-17",
            tier="standard",
            languages=frozenset({"zh", "en", "ja", "ko", "yue"}),
            installed=(
                "sherpa-onnx-standard" in installed_ids
                and "sensevoice-small-int8" in installed_models
            ),
            formal_default=is_formal_default("sensevoice-small-int8", "2024-07-17"),
        ),
        ProviderOption(
            provider_id="whisper-cpp-base",
            model_id="whisper-cpp-base",
            model_version="80da2d8",
            tier="multilingual",
            languages=frozenset({"*"}),
            installed=(
                "whisper-cpp-base" in installed_ids
                and "whisper-cpp-base" in installed_models
            ),
            formal_default=is_formal_default("whisper-cpp-base", "80da2d8"),
        ),
    ]
    known = {option.provider_id for option in options}
    for provider_id in sorted(installed_ids - known):
        provider = get_provider(provider_id)
        if provider is None:
            continue
        capabilities = await provider.inspect()
        options.append(
            ProviderOption(
                provider_id=provider_id,
                model_id=capabilities.model_id,
                model_version=capabilities.model_version,
                tier="custom",
                languages=capabilities.languages,
                installed=True,
                # Custom/user providers never inherit a formal-default approval.
                formal_default=False,
            )
        )
    return tuple(options)


class CleaningTranscriptHandler:
    """Clean the fetched transcript and store the result inline."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    async def execute(self, job: Job, session: AsyncSession, ctx: StageContext) -> StageOutcome:
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

    async def execute(self, job: Job, session: AsyncSession, ctx: StageContext) -> StageOutcome:
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
    """Summarize each chunk with the LLM provider, checkpointing per chunk."""

    def __init__(self, store: ArtifactStore, llm: LLMProvider) -> None:
        self._store = store
        self._llm = llm

    async def execute(self, job: Job, session: AsyncSession, ctx: StageContext) -> StageOutcome:
        fingerprint = _stage_fingerprint(job.url)
        raw = await _load_json_artifact(
            session, self._store, job.job_id, CHUNKS_ARTIFACT_TYPE, fingerprint
        )
        if raw is None:
            return StageOutcome.fatal("INTERNAL_ERROR", error_detail="missing chunks artifact")

        chunks = json.loads(raw)
        await session.commit()
        summaries: list[str] = []
        try:
            for chunk in chunks:
                if await ctx.is_cancelled():
                    return StageOutcome.fatal("CANCELLED_BY_USER", error_detail="cancelled")
                chunk_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                chunk_fp = _stage_fingerprint(f"{chunk_hash}|{job.config_fingerprint}")
                existing = await get_artifact(
                    session,
                    job_id=job.job_id,
                    artifact_type=CHUNK_SUMMARY_ARTIFACT_TYPE,
                    input_fingerprint=chunk_fp,
                )
                await session.commit()
                if existing is not None and existing.payload_json is not None:
                    summaries.append(existing.payload_json)
                else:
                    summary = await self._llm.complete(_summary_prompt(chunk))
                    await save_artifact_inline(
                        session,
                        job_id=job.job_id,
                        owner=ctx.owner,
                        now=ctx.now(),
                        stage="summarizing_chunks",
                        artifact_type=CHUNK_SUMMARY_ARTIFACT_TYPE,
                        input_fingerprint=chunk_fp,
                        schema_version=1,
                        payload_json=summary,
                    )
                    summaries.append(summary)
                if not await ctx.renew_lease():
                    return StageOutcome.fatal("ENGINE_RESTARTED", error_detail="lease lost")
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
    """Synthesize, render, and atomically write the Markdown report."""

    def __init__(self, store: ArtifactStore, writer: ReportWriter, llm: LLMProvider) -> None:
        self._store = store
        self._writer = writer
        self._llm = llm

    async def execute(self, job: Job, session: AsyncSession, ctx: StageContext) -> StageOutcome:
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
        transcript_raw = await _load_json_artifact(
            session, self._store, job.job_id, CLEANED_ARTIFACT_TYPE, fingerprint
        )
        transcript = transcript_from_json(transcript_raw) if transcript_raw else None
        await session.commit()
        try:
            synthesis = await synthesize(self._llm, summaries)
        except LLMError as exc:
            return _llm_outcome(exc)
        except SynthesisError as exc:
            return StageOutcome.fatal("INTERNAL_ERROR", error_detail=str(exc))

        doc = ReportDocument(
            analysis_id=job.job_id,
            source_url=cast(HttpUrl, job.url),
            platform=metadata["platform"],
            video_id=metadata["video_id"],
            title=metadata["title"],
            author=metadata.get("author", ""),
            analyzed_at=_now(),
            summary_mode=cast(Literal["auto", "standard", "unboxing"], job.mode),
            language=transcript.language if transcript else "",
            asr_provider=job.asr_provider_id or "",
            asr_model=job.asr_model_id or "",
            asr_model_version=job.asr_model_version or "",
            core_summary=synthesis.core_summary,
            key_takeaways=synthesis.key_takeaways,
            timeline_outline=synthesis.timeline_outline,
            content_analysis=synthesis.content_analysis,
        )
        markdown = render_markdown(doc)
        content = markdown.encode("utf-8")
        content_hash = hashlib.sha256(content).hexdigest()
        filename = report_filename(doc.title, job.job_id, content_hash)

        previous = await get_artifact(
            session,
            job_id=job.job_id,
            artifact_type=REPORT_ARTIFACT_TYPE,
            input_fingerprint=fingerprint,
        )
        previous_hash = previous.content_hash if previous else None

        try:
            result = await self._writer.write_report(filename, markdown, previous_hash)
        except ReportConflictError as exc:
            return StageOutcome.fatal("REPORT_CONFLICT", error_detail=str(exc))

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
    """Verify the generated report, index it, and complete the job.

    The report file is read back, parsed with the frozen Schema v1 parser, and
    written into ``report_documents`` + ``report_fts`` through the pairing
    repository (docs/FTS5_SCHEMA.md §5). The transaction is committed here —
    the repository functions never commit on their own.
    """

    def __init__(self, writer: ReportWriter) -> None:
        self._writer = writer

    async def execute(self, job: Job, session: AsyncSession, ctx: StageContext) -> StageOutcome:
        fingerprint = _stage_fingerprint(job.url)
        artifact = await get_artifact(
            session,
            job_id=job.job_id,
            artifact_type=REPORT_ARTIFACT_TYPE,
            input_fingerprint=fingerprint,
        )
        if (
            artifact is None
            or artifact.relative_path is None
            or artifact.content_hash is None
        ):
            return StageOutcome.fatal("INTERNAL_ERROR", error_detail="missing report artifact")
        try:
            content = await self._writer.read_file_verified(
                artifact.relative_path, artifact.content_hash
            )
        except ArtifactIntegrityError as exc:
            return StageOutcome.fatal("INDEX_FAILED", error_detail=str(exc))
        try:
            parsed = decode_report_bytes(content.encode("utf-8"))
        except MarkdownParseError as exc:
            # The pipeline wrote this file itself; an unparseable report is an
            # internal defect, not a retryable condition.
            return StageOutcome.fatal(
                "INDEX_PARSE_FAILED", error_detail=f"{exc.code}: {exc}"
            )
        entry = ReportIndexEntry(
            job_id=job.job_id,
            analysis_id=parsed.analysis_id,
            title=parsed.title,
            platform=parsed.platform,
            author=parsed.author,
            video_id=parsed.video_id,
            source_url=parsed.source_url,
            published_at=(
                parsed.published_at.timestamp() if parsed.published_at else None
            ),
            analyzed_at=parsed.analyzed_at.timestamp(),
            summary_mode=parsed.summary_mode,
            language=parsed.language,
            asr_provider=parsed.asr_provider,
            asr_model=parsed.asr_model,
            asr_model_version=parsed.asr_model_version,
            tags=parsed.tags,
            summary_preview=parsed.core_summary[:200],
            relative_path=artifact.relative_path,
            content_hash=artifact.content_hash,
            byte_size=artifact.byte_size,
            doc_source="pipeline",
        )
        # BUSY_SNAPSHOT guard (§6): get_artifact above opened a deferred read
        # transaction — closed explicitly before the write (the helper
        # refuses an open transaction); the index write then runs as
        # IMMEDIATE so a concurrent writer (rescan batch, settings save)
        # committing in between cannot kill this flush with an unretryable
        # snapshot conflict.
        await session.commit()
        async with immediate_write_transaction(session):
            await upsert_report_document(
                session,
                entry=entry,
                body=FtsBodyTexts(
                    summary=parsed.core_summary,
                    transcript=parsed.transcript or "",
                ),
                now=time.time(),
            )
        return StageOutcome.success(target=JobStatus.COMPLETED)


def _summary_prompt(chunk: str) -> str:
    return f"Summarize the following transcript chunk concisely:\n\n{chunk}"


def _now() -> datetime:
    return datetime.now(UTC)
