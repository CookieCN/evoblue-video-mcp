"""Canonical job states shared by API, worker, and MCP schemas."""

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    FETCHING_METADATA = "fetching_metadata"
    FETCHING_SUBTITLES = "fetching_subtitles"
    DOWNLOADING_AUDIO = "downloading_audio"
    TRANSCRIBING = "transcribing"
    WAITING_FOR_MODEL = "waiting_for_model"
    CLEANING_TRANSCRIPT = "cleaning_transcript"
    CHUNKING = "chunking"
    SUMMARIZING_CHUNKS = "summarizing_chunks"
    GENERATING_REPORT = "generating_report"
    INDEXING = "indexing"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)
ACTIVE_JOB_STATUSES = frozenset(JobStatus) - TERMINAL_JOB_STATUSES
