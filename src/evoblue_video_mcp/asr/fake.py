"""Deterministic in-memory ASR provider for tests and the benchmark smoke run.

``FakeASRProvider`` honors the behavioral contract from
:mod:`evoblue_video_mcp.asr.base`: it checks ``is_cancelled`` before each segment,
reports monotonic ``on_progress`` values, and supports ``on_segment`` /
``resume_keys`` checkpointing so contract tests exercise the same semantics a
real provider must implement.
"""

from evoblue_video_mcp.asr.base import (
    ASR_CANCELLED,
    ASRCapabilities,
    ASRError,
    ASRRequest,
    ASRResult,
    ASRSegment,
    CancelCheck,
    ProgressCallback,
    SegmentCallback,
    segment_key,
)


class FakeASRProvider:
    """Returns a fixed transcription; deterministic for the same construction."""

    provider_id = "fake"

    def __init__(
        self,
        segments: list[ASRSegment] | None = None,
        detected_language: str = "zh",
        model_id: str = "fake-model",
        model_version: str = "1",
        languages: frozenset[str] = frozenset({"zh", "en"}),
        platforms: frozenset[str] = frozenset({"windows-x86_64"}),
    ) -> None:
        self._segments = (
            list(segments)
            if segments is not None
            else [ASRSegment(start=0.0, end=1.0, text="你好")]
        )
        self._detected_language = detected_language
        self._model_id = model_id
        self._model_version = model_version
        self._languages = languages
        self._platforms = platforms

    async def inspect(self) -> ASRCapabilities:
        return ASRCapabilities(
            provider_id=self.provider_id,
            model_id=self._model_id,
            model_version=self._model_version,
            languages=self._languages,
            platforms=self._platforms,
        )

    async def transcribe(
        self,
        request: ASRRequest,
        *,
        is_cancelled: CancelCheck | None = None,
        on_progress: ProgressCallback | None = None,
        on_segment: SegmentCallback | None = None,
        resume_keys: frozenset[str] | None = None,
    ) -> ASRResult:
        resume = resume_keys or frozenset()
        total = len(self._segments)
        pending: list[ASRSegment] = []
        for index, segment in enumerate(self._segments):
            if is_cancelled is not None and await is_cancelled():
                raise ASRError(ASR_CANCELLED, "transcription cancelled")
            if on_progress is not None:
                await on_progress((index + 1) / total)
            if segment_key(segment.start, segment.end) in resume:
                continue
            if on_segment is not None:
                await on_segment(segment)
            pending.append(segment)
        if total == 0 and on_progress is not None:
            await on_progress(1.0)
        return ASRResult(
            segments=tuple(pending),
            detected_language=self._detected_language,
            provider_id=self.provider_id,
            model_id=self._model_id,
            model_version=self._model_version,
        )
