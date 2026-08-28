"""Platform adapter protocol and stable error codes."""

from typing import Protocol

from evoblue_video_mcp.platforms.models import Transcript, VideoMetadata, VideoRef

METADATA_FETCH_FAILED = "METADATA_FETCH_FAILED"
SUBTITLE_UNAVAILABLE = "SUBTITLE_UNAVAILABLE"
SUBTITLE_MISSING = "SUBTITLE_MISSING"
AUDIO_DOWNLOAD_FAILED = "AUDIO_DOWNLOAD_FAILED"
FFMPEG_MISSING = "FFMPEG_MISSING"


class AdapterError(Exception):
    """Raised when an adapter cannot fetch metadata, subtitles, or audio."""

    def __init__(self, error_code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class PlatformAdapter(Protocol):
    """Fetches metadata, transcripts, and audio for one or more video platforms."""

    def supports(self, ref: VideoRef) -> bool: ...

    async def fetch_metadata(self, ref: VideoRef) -> VideoMetadata: ...

    async def fetch_transcript(self, ref: VideoRef) -> Transcript: ...

    async def download_audio(self, ref: VideoRef, dest_path: str) -> None: ...
