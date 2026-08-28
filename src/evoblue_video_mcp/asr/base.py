"""ASR provider contract, normalized result schema, and stable error codes.

The provider boundary has two layers:

1. :class:`ASRProvider` declares what a provider exposes and the *behavioral*
   contract of ``transcribe`` — cancellation and progress semantics, documented
   on the method. Implementations wrap a real or fake ASR engine.

2. :class:`ASRResult` / :class:`ASRSegment` are Pydantic models that enforce the
   *normalized* shape at the boundary: finite non-negative timestamps, valid
   intervals, monotonic ordering, and non-empty provider identity. A provider
   returning malformed output cannot poison downstream checkpoints, cleaning, or
   Markdown rendering.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

ASR_MODEL_MISSING = "ASR_MODEL_MISSING"
ASR_LANGUAGE_UNSUPPORTED = "ASR_LANGUAGE_UNSUPPORTED"
ASR_TRANSCRIPTION_FAILED = "ASR_TRANSCRIPTION_FAILED"
ASR_AUDIO_INVALID = "ASR_AUDIO_INVALID"
ASR_CANCELLED = "ASR_CANCELLED"


class ASRError(Exception):
    """Raised when an ASR provider cannot transcribe audio."""

    def __init__(self, error_code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class ASRSegment(BaseModel):
    """One timed segment of recognized speech with normalized invariants."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str = ""

    @model_validator(mode="after")
    def _validate_interval(self) -> "ASRSegment":
        if self.end < self.start:
            raise ValueError("segment end must be >= start")
        return self


def segment_key(start: float, end: float) -> str:
    """Return a stable checkpoint key derived from a segment's boundaries."""
    return f"{start:.3f}:{end:.3f}"


class ASRResult(BaseModel):
    """Normalized transcription output; provider-specific raw output stays outside."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    segments: tuple[ASRSegment, ...]
    detected_language: str = ""
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = ""
    warnings: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_monotonic(self) -> "ASRResult":
        if any(later.start < earlier.start for earlier, later in pairwise(self.segments)):
            raise ValueError("segments must be ordered by non-decreasing start time")
        return self


@dataclass(frozen=True)
class ASRRequest:
    """A transcription request against a local audio file."""

    audio_path: str
    language: str | None = None


@dataclass(frozen=True)
class ASRCapabilities:
    """Identity and supported surface of an installed ASR provider."""

    provider_id: str
    model_id: str
    model_version: str
    languages: frozenset[str]
    platforms: frozenset[str]


ProgressCallback = Callable[[float], Awaitable[None]]
CancelCheck = Callable[[], Awaitable[bool]]
SegmentCallback = Callable[[ASRSegment], Awaitable[None]]


class ASRProvider(Protocol):
    """Transcribes local audio; implementations wrap a real or fake ASR engine."""

    provider_id: str

    async def inspect(self) -> ASRCapabilities: ...

    async def transcribe(
        self,
        request: ASRRequest,
        *,
        is_cancelled: CancelCheck | None = None,
        on_progress: ProgressCallback | None = None,
        on_segment: SegmentCallback | None = None,
        resume_keys: frozenset[str] | None = None,
    ) -> ASRResult:
        """Transcribe ``request`` into a normalized :class:`ASRResult`.

        Behavioral contract:

        - ``is_cancelled``, when given, is awaited before each unit of work (at
          minimum before each recognized segment). If it returns ``True``, the
          provider must raise :class:`ASRError` with ``ASR_CANCELLED`` rather
          than silently returning a partial result.
        - ``on_progress``, when given, is awaited with values in ``[0.0, 1.0]``
          that never decrease, ending at ``1.0`` once transcription completes.
        - ``on_segment``, when given, is awaited once per completed segment so
          the caller can checkpoint it incrementally before the whole result is
          returned.
        - ``resume_keys``, when given, names already-checkpointed segments by
          their stable ``start:end`` key; the provider skips those and returns
          only the newly transcribed segments.
        """
        ...
