"""ASR provider contract: fake provider, error codes, normalized result."""

import pytest
from pydantic import ValidationError

from evoblue_video_mcp.asr.base import (
    ASR_AUDIO_INVALID,
    ASR_CANCELLED,
    ASR_MODEL_MISSING,
    ASRError,
    ASRRequest,
    ASRResult,
    ASRSegment,
    segment_key,
)
from evoblue_video_mcp.asr.fake import FakeASRProvider


async def test_fake_provider_returns_fixed_segments() -> None:
    provider = FakeASRProvider(
        segments=[ASRSegment(start=0.0, end=1.5, text="你好世界")],
        detected_language="zh",
    )
    result = await provider.transcribe(ASRRequest(audio_path="/tmp/a.wav"))
    assert result.segments == (ASRSegment(start=0.0, end=1.5, text="你好世界"),)
    assert result.detected_language == "zh"
    assert result.provider_id == "fake"


async def test_fake_provider_is_deterministic() -> None:
    provider = FakeASRProvider()
    first = await provider.transcribe(ASRRequest(audio_path="x"))
    second = await provider.transcribe(ASRRequest(audio_path="x"))
    assert first == second


async def test_fake_provider_inspect_reports_capabilities() -> None:
    provider = FakeASRProvider(languages=frozenset({"zh", "en"}))
    caps = await provider.inspect()
    assert caps.provider_id == "fake"
    assert "zh" in caps.languages
    assert "windows-x86_64" in caps.platforms


async def test_fake_provider_reports_monotonic_progress() -> None:
    provider = FakeASRProvider(
        segments=[
            ASRSegment(start=0.0, end=1.0, text="a"),
            ASRSegment(start=1.0, end=2.0, text="b"),
            ASRSegment(start=2.0, end=3.0, text="c"),
        ]
    )
    progress: list[float] = []

    async def track(value: float) -> None:
        progress.append(value)

    await provider.transcribe(ASRRequest(audio_path="x"), on_progress=track)
    assert progress == [1 / 3, 2 / 3, 1.0]
    assert all(0.0 <= value <= 1.0 for value in progress)


async def test_fake_provider_cancels_on_request() -> None:
    provider = FakeASRProvider(
        segments=[
            ASRSegment(start=0.0, end=1.0, text="a"),
            ASRSegment(start=1.0, end=2.0, text="b"),
        ]
    )

    async def cancelled() -> bool:
        return True

    with pytest.raises(ASRError) as exc_info:
        await provider.transcribe(ASRRequest(audio_path="x"), is_cancelled=cancelled)
    assert exc_info.value.error_code == ASR_CANCELLED


async def test_fake_provider_zero_segments_completes() -> None:
    provider = FakeASRProvider(segments=[])
    progress: list[float] = []

    async def track(value: float) -> None:
        progress.append(value)

    result = await provider.transcribe(ASRRequest(audio_path="x"), on_progress=track)
    assert result.segments == ()
    assert progress == [1.0]


async def test_fake_provider_resumes_from_checkpoint() -> None:
    provider = FakeASRProvider(
        segments=[
            ASRSegment(start=0.0, end=1.0, text="a"),
            ASRSegment(start=1.0, end=2.0, text="b"),
        ]
    )
    collected: list[str] = []

    async def on_seg(segment: ASRSegment) -> None:
        collected.append(segment.text)

    result = await provider.transcribe(
        ASRRequest(audio_path="x"),
        on_segment=on_seg,
        resume_keys=frozenset({segment_key(0.0, 1.0)}),
    )
    assert [s.text for s in result.segments] == ["b"]
    assert collected == ["b"]


def test_asr_error_codes_and_retryability() -> None:
    missing = ASRError(ASR_MODEL_MISSING, "no model")
    assert missing.error_code == ASR_MODEL_MISSING
    assert missing.retryable is False

    invalid = ASRError(ASR_AUDIO_INVALID, "bad audio")
    assert invalid.error_code == ASR_AUDIO_INVALID
    assert invalid.retryable is False


def test_result_is_immutable() -> None:
    result = ASRResult(
        segments=[ASRSegment(start=0.0, end=1.0, text="a")],
        provider_id="fake",
        model_id="m",
    )
    assert isinstance(result.segments, tuple)
    with pytest.raises(AttributeError):
        result.segments.reverse()
    with pytest.raises(ValidationError):
        result.segments[0].start = -9.0


def test_result_rejects_negative_timestamp() -> None:
    with pytest.raises(ValidationError):
        ASRSegment(start=-1.0, end=1.0, text="x")


def test_result_rejects_nan_timestamp() -> None:
    with pytest.raises(ValidationError):
        ASRSegment(start=float("nan"), end=1.0, text="x")


def test_result_rejects_infinite_timestamp() -> None:
    with pytest.raises(ValidationError):
        ASRSegment(start=0.0, end=float("inf"), text="x")


def test_result_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        ASRSegment(start=2.0, end=1.0, text="x")


def test_result_rejects_unordered_segments() -> None:
    with pytest.raises(ValidationError):
        ASRResult(
            segments=[
                ASRSegment(start=2.0, end=3.0, text="b"),
                ASRSegment(start=0.0, end=1.0, text="a"),
            ],
            provider_id="fake",
            model_id="m",
        )


def test_result_requires_provider_identity() -> None:
    with pytest.raises(ValidationError):
        ASRResult(
            segments=[ASRSegment(start=0.0, end=1.0, text="x")],
            provider_id="",
            model_id="m",
        )
