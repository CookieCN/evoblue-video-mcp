"""Scoring metrics: CER, WER, and segment boundary error."""

import pytest

from evoblue_video_mcp.asr.benchmark.metrics import (
    SegmentBoundary,
    character_error_rate,
    entity_recall,
    normalize_for_scoring,
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


def test_entity_recall_counts_exact_hits_case_insensitive() -> None:
    zh_text = "很多人在用 chatgpt 写文案，提到亚马逊。"  # noqa: RUF001
    assert entity_recall(["ChatGPT", "亚马逊"], zh_text) == 1.0


def test_entity_recall_ignores_surrounding_punctuation() -> None:
    assert entity_recall(["TikTok Shop"], "重点讲「TikTok Shop」的扩张。") == 1.0


def test_entity_recall_miss_is_counted() -> None:
    assert entity_recall(["亚马逊", "eBay"], "只有亚马逊。") == 0.5


def test_entity_recall_empty_entities_is_zero_not_perfect() -> None:
    assert entity_recall([], "任意文本") == 0.0


def test_normalize_for_scoring_strips_punctuation_and_case() -> None:
    assert normalize_for_scoring("大家好，Hello World！")  # noqa: RUF001 == "大家好helloworld"


def test_normalize_for_scoring_folds_fullwidth_digits() -> None:
    assert normalize_for_scoring("ＴｉｋＴｏｋ２０２４年") == "tiktok2024年"


def test_entity_recall_excluded_from_aggregation_without_entities() -> None:
    from evoblue_video_mcp.asr.release_gate import LITE_THRESHOLDS, evaluate_gate

    report = {
        "results": [
            {"language": "zh", "cer": 0.05, "entity_count": 0, "entity_recall": 0.0,
             "rtf": 0.1, "empty_reference": False},
            {"language": "zh", "cer": 0.05, "entity_count": 2, "entity_recall": 0.5,
             "rtf": 0.1, "empty_reference": False},
        ],
    }
    verdict = evaluate_gate(report, LITE_THRESHOLDS)
    items = {i.name: i for i in verdict.items}
    # mean recall over entity-bearing samples only = 0.5 -> lite fails at 0.60
    assert not items["entity_recall"].passed
    assert "over 1 samples" in items["entity_recall"].detail
