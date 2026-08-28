"""Scoring metrics: CER, WER, and segment boundary error."""

import pytest

from evoblue_video_mcp.asr.benchmark.metrics import (
    SegmentBoundary,
    character_error_rate,
    segment_boundary_error,
    word_error_rate,
)


def test_cer_identical_is_zero() -> None:
    assert character_error_rate("你好世界", "你好世界") == 0.0


def test_cer_one_substitution() -> None:
    assert character_error_rate("你好世界", "你好世朋") == 0.25


def test_wer_identical_is_zero() -> None:
    assert word_error_rate("hello world", "hello world") == 0.0


def test_wer_one_deletion() -> None:
    assert word_error_rate("hello world", "hello") == 0.5


def test_segment_boundary_error_aligned() -> None:
    ref = [SegmentBoundary(start=0.0, end=1.0), SegmentBoundary(start=1.0, end=2.0)]
    hyp = [SegmentBoundary(start=0.2, end=1.1), SegmentBoundary(start=1.1, end=2.2)]
    # aligned error = 0.6 seconds across 4 boundaries
    assert segment_boundary_error(ref, hyp) == pytest.approx(0.15)


def test_segment_boundary_error_penalizes_missing_segments() -> None:
    ref = [SegmentBoundary(start=0.0, end=1.0), SegmentBoundary(start=1.0, end=2.0)]
    hyp = [SegmentBoundary(start=0.2, end=1.1)]
    # aligned error 0.3 + one missing segment penalty (2 * mean_duration 1.0) = 2.3 over 4
    assert segment_boundary_error(ref, hyp) == pytest.approx(0.575)


def test_segment_boundary_error_penalizes_hallucinated_segments() -> None:
    ref = [SegmentBoundary(start=0.0, end=1.0)]
    hyp = [SegmentBoundary(start=0.0, end=1.0), SegmentBoundary(start=1.0, end=2.0)]
    # aligned error 0 + one extra segment penalty (2 * mean_duration 1.0) = 2.0 over 4
    assert segment_boundary_error(ref, hyp) == pytest.approx(0.5)


def test_cer_silence_with_hallucination() -> None:
    assert character_error_rate("", "你好") == 1.0


def test_cer_silence_correct() -> None:
    assert character_error_rate("", "") == 0.0


def test_wer_silence_with_hallucination() -> None:
    assert word_error_rate("", "hello") == 1.0


def test_segment_boundary_error_silence_with_hallucination() -> None:
    assert segment_boundary_error([], [SegmentBoundary(start=0.0, end=1.0)]) == 1.0


def test_segment_boundary_error_silence_correct() -> None:
    assert segment_boundary_error([], []) == 0.0
