"""Platform-neutral data models for video metadata and transcripts."""

from dataclasses import dataclass, field
from enum import StrEnum


class Platform(StrEnum):
    YOUTUBE = "youtube"
    BILIBILI = "bilibili"


@dataclass(frozen=True)
class VideoRef:
    """A resolved reference to one video on one supported platform."""

    platform: Platform
    video_id: str
    url: str


@dataclass(frozen=True)
class VideoMetadata:
    """Basic metadata fetched from a video platform."""

    video_id: str
    platform: Platform
    title: str
    author: str = ""
    duration: float | None = None
    published_at: str | None = None


@dataclass(frozen=True)
class TranscriptSegment:
    """One timed segment of a transcript."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcript:
    """A timed transcript in one language."""

    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str = ""
    source: str = ""  # e.g. "youtube-manual", "youtube-auto", "bilibili"
