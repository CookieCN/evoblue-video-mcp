"""Pipeline stage handlers for metadata and subtitle acquisition."""

import hashlib
import json

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.platforms.base import AdapterError, PlatformAdapter
from evoblue_video_mcp.platforms.detector import PlatformError, detect_video
from evoblue_video_mcp.platforms.models import Transcript, VideoMetadata
from evoblue_video_mcp.runtime.worker import ArtifactRecord, StageOutcome
from evoblue_video_mcp.storage.artifact_store import ArtifactStore
from evoblue_video_mcp.storage.models import Job

METADATA_ARTIFACT_TYPE = "video_metadata"
TRANSCRIPT_ARTIFACT_TYPE = "platform_transcript"


def _stage_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _adapter_outcome(exc: AdapterError) -> StageOutcome:
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


class FetchingMetadataHandler:
    """Fetch video metadata and register it as an inline JSON artifact."""

    def __init__(self, adapter: PlatformAdapter) -> None:
        self._adapter = adapter

    async def execute(self, job: Job) -> StageOutcome:
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

    async def execute(self, job: Job) -> StageOutcome:
        try:
            ref = detect_video(job.url)
            transcript = await self._adapter.fetch_transcript(ref)
        except PlatformError as exc:
            return StageOutcome.fatal(exc.error_code, error_detail=str(exc))
        except AdapterError as exc:
            return _adapter_outcome(exc)

        relative_path = f"{job.job_id}/{TRANSCRIPT_ARTIFACT_TYPE}.json"
        content = transcript_to_json(transcript).encode("utf-8")
        content_hash, byte_size = await self._store.write_file(relative_path, content)

        return StageOutcome.success(
            target=JobStatus.CLEANING_TRANSCRIPT,
            artifact=ArtifactRecord(
                stage="fetching_subtitles",
                artifact_type=TRANSCRIPT_ARTIFACT_TYPE,
                input_fingerprint=_stage_fingerprint(job.url),
                schema_version=1,
                storage_kind="file",
                relative_path=relative_path,
                content_hash=content_hash,
                byte_size=byte_size,
            ),
        )
