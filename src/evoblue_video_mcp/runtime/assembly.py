"""Assemble the pipeline handler set from its dependencies."""

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.llm.base import LLMProvider
from evoblue_video_mcp.platforms.base import PlatformAdapter
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
from evoblue_video_mcp.runtime.worker import StageHandler
from evoblue_video_mcp.storage.artifact_store import ArtifactStore


def build_handlers(
    *,
    adapter: PlatformAdapter,
    artifact_store: ArtifactStore,
    report_writer: ReportWriter,
    llm: LLMProvider,
    asr_provider_id: str = "auto",
) -> dict[JobStatus, StageHandler]:
    """Assemble the full P2/ASR pipeline handler set."""
    return {
        JobStatus.FETCHING_METADATA: FetchingMetadataHandler(adapter),
        JobStatus.FETCHING_SUBTITLES: FetchingSubtitlesHandler(adapter, artifact_store),
        JobStatus.DOWNLOADING_AUDIO: DownloadingAudioHandler(adapter, artifact_store),
        JobStatus.TRANSCRIBING: TranscribingHandler(artifact_store, asr_provider_id),
        JobStatus.CLEANING_TRANSCRIPT: CleaningTranscriptHandler(artifact_store),
        JobStatus.CHUNKING: ChunkingHandler(artifact_store),
        JobStatus.SUMMARIZING_CHUNKS: SummarizingChunksHandler(artifact_store, llm),
        JobStatus.GENERATING_REPORT: GeneratingReportHandler(artifact_store, report_writer, llm),
        JobStatus.INDEXING: IndexingHandler(report_writer),
    }
