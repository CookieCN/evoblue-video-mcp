"""Release-gate threshold evaluation (frozen marks, fail-closed semantics)."""

from evoblue_video_mcp.asr.release_gate import (
    LITE_THRESHOLDS,
    STANDARD_THRESHOLDS,
    THRESHOLDS_VERSION,
    evaluate_gate,
)


def _result(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "sample_id": "zh-001",
        "language": "zh",
        "cer": 0.02,
        "wer": 0.0,
        "entity_count": 1,
        "entity_recall": 1.0,
        "rtf": 0.10,
        "empty_reference": False,
        "hallucinated": False,
    }
    base.update(overrides)
    return base


def _report(results: list[dict[str, object]]) -> dict[str, object]:
    return {"corpus": "t", "provider": "p", "results": results}


def test_thresholds_are_frozen_at_v1() -> None:
    assert THRESHOLDS_VERSION == "v1"
    assert STANDARD_THRESHOLDS.max_cer == 0.10
    assert LITE_THRESHOLDS.max_cer == 0.25
    assert STANDARD_THRESHOLDS.max_hallucination_rate == 0.0


def test_passing_report_passes_standard_gate() -> None:
    report = _report(
        [
            _result(),
            _result(sample_id="en-001", language="en", cer=0.5, wer=0.04),
            _result(sample_id="zh-silence", empty_reference=True, hallucinated=False),
        ]
    )
    verdict = evaluate_gate(report, STANDARD_THRESHOLDS)
    assert verdict.passed
    assert all(item.passed for item in verdict.items)


def test_cer_failure_names_the_metric() -> None:
    report = _report([_result(cer=0.9)])
    verdict = evaluate_gate(report, STANDARD_THRESHOLDS)
    assert not verdict.passed
    failed = [item.name for item in verdict.items if not item.passed]
    assert "chinese_cer" in failed


def test_missing_english_fails_standard_but_not_lite_scope() -> None:
    report = _report([_result()])
    assert not evaluate_gate(report, STANDARD_THRESHOLDS).passed
    # English absence is expressed as a failed item, never as a silent skip.
    items = {item.name: item for item in evaluate_gate(report, STANDARD_THRESHOLDS).items}
    assert items["english_wer"].detail.startswith("no en samples")


def test_hallucinated_silence_fails_gate() -> None:
    report = _report(
        [
            _result(),
            _result(sample_id="en-001", language="en", wer=0.01),
            _result(sample_id="zh-music", empty_reference=True, hallucinated=True),
        ]
    )
    verdict = evaluate_gate(report, STANDARD_THRESHOLDS)
    assert not verdict.passed
    items = {item.name: item for item in verdict.items}
    assert not items["hallucination_rate"].passed


def test_entity_recall_threshold_is_evaluated() -> None:
    report = _report(
        [
            _result(entity_recall=0.5),
            _result(sample_id="en-001", language="en", wer=0.01, entity_recall=0.4),
        ]
    )
    verdict = evaluate_gate(report, LITE_THRESHOLDS)
    items = {item.name: item for item in verdict.items}
    assert not items["entity_recall"].passed  # mean 0.45 < lite's 0.60


def test_rtf_breach_fails_gate() -> None:
    report = _report(
        [
            _result(rtf=0.9),
            _result(sample_id="en-001", language="en", wer=0.01),
        ]
    )
    assert not evaluate_gate(report, LITE_THRESHOLDS).passed


def test_empty_report_fails_closed() -> None:
    verdict = evaluate_gate(_report([]), STANDARD_THRESHOLDS)
    assert not verdict.passed
    assert len(verdict.items) == 5
    assert all(not item.passed for item in verdict.items)


def test_malformed_samples_do_not_crash_evaluation() -> None:
    report: dict[str, object] = {
        "corpus": "t",
        "provider": "p",
        "results": ["not-a-dict", 42, _result()],
    }
    verdict = evaluate_gate(report, LITE_THRESHOLDS)
    assert isinstance(verdict.passed, bool)
