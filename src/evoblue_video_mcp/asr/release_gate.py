"""Default-model release gate: frozen thresholds and pass/fail evaluation.

ASR_PLAN section 7 requires that a tier becomes a formal default only after a
benchmark gate with *frozen* thresholds. This module is the machine-checkable
half of that contract: the thresholds live in code (typed, test-covered, never
tuned after seeing a failing run) and :func:`evaluate_gate` turns a benchmark
report into a verdict with per-item reasons.

The human half — recorded evidence, license review and the approval itself —
lives in ``docs/ASR_RELEASE_GATE.md`` and :mod:`evoblue_video_mcp.asr.approvals`.
Thresholds are frozen for the TTS-synthesized EvoBlue corpus v1 (clean studio
conditions); the corpus is a quality *lower bound*, so a model that passes here
still needs real-world spot checks before being marketed as best quality.
"""

from dataclasses import dataclass

THRESHOLDS_VERSION = "v1"


@dataclass(frozen=True)
class GateThresholds:
    """Frozen pass marks for one tier on one frozen corpus.

    ``languages`` scopes the gate to the tier's manifest coverage: a
    Chinese-only Lite tier is never judged on English samples, while the
    multilingual Standard tier fails closed if its covered languages were not
    measured. ``max_cer``/``max_wer`` apply to speech samples of the matching
    language (zh CER, en WER); ``min_entity_recall`` to samples that declare
    entities; ``max_rtf`` bounds real-time factor on the benchmark machine's
    documented CPU tier. Silence/music-only samples are evaluated by
    ``max_hallucination_rate``: the share of empty-reference samples where the
    provider emitted any text.
    """

    version: str
    languages: frozenset[str]
    max_cer: float
    max_wer: float
    min_entity_recall: float
    max_rtf: float
    max_hallucination_rate: float


# Frozen 2026-08-28 from TTS-corpus-v1 magnitude runs (see
# docs/ASR_RELEASE_GATE.md): SenseVoice on clean synthesized Mandarin scores
# CER 0.00 with entity recall 1.0; the thresholds add headroom for
# deterministic TTS variation across voice versions while still catching real
# regressions. Lite is labeled "fast trial quality", so its marks are
# deliberately looser; its scope excludes English, which routing never sends
# to a zh-only tier (that contract has its own routing tests).
STANDARD_THRESHOLDS = GateThresholds(
    version=THRESHOLDS_VERSION,
    languages=frozenset({"zh", "en"}),
    max_cer=0.10,
    max_wer=0.15,
    min_entity_recall=0.80,
    max_rtf=0.30,
    max_hallucination_rate=0.0,
)

LITE_THRESHOLDS = GateThresholds(
    version=THRESHOLDS_VERSION,
    languages=frozenset({"zh"}),
    max_cer=0.25,
    max_wer=0.25,
    min_entity_recall=0.60,
    max_rtf=0.15,
    max_hallucination_rate=0.0,
)


@dataclass(frozen=True)
class GateItem:
    """One evaluated gate line and why it passed or failed."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class GateVerdict:
    """Overall gate result with every evaluated item attached."""

    passed: bool
    items: tuple[GateItem, ...]


def _result_dicts(report: dict[str, object]) -> list[dict[str, object]]:
    """Return the report's per-sample entries, ignoring malformed shapes."""
    samples = report.get("results")
    if not isinstance(samples, list):
        return []
    return [sample for sample in samples if isinstance(sample, dict)]


def _aggregate(report: dict[str, object], key: str) -> tuple[float, float, int]:
    """Return (mean, worst, count) of a per-sample metric, ignoring absences."""
    values: list[float] = []
    worst = 0.0
    for sample in _result_dicts(report):
        value = sample.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
            worst = max(worst, float(value))
    if not values:
        return (0.0, 0.0, 0)
    return (sum(values) / len(values), worst, len(values))


def _aggregate_entities(report: dict[str, object]) -> tuple[float, float, int]:
    """Aggregate entity recall over samples that actually declare entities.

    Samples without declared entities are excluded rather than counted as
    zero-recall: "no entities to find" is not a recognition failure.
    """
    values: list[float] = []
    worst = 0.0
    for sample in _result_dicts(report):
        count = sample.get("entity_count")
        if not isinstance(count, (int, float)) or count < 1:
            continue
        value = sample.get("entity_recall")
        if isinstance(value, (int, float)):
            values.append(float(value))
            worst = max(worst, float(value))
    if not values:
        return (0.0, 0.0, 0)
    return (sum(values) / len(values), worst, len(values))


def _hallucination_rate(report: dict[str, object]) -> float | None:
    """Rate of empty-reference samples that produced text, or ``None`` if the
    corpus measured none — a vacuous item must not read as a passing one."""
    samples = report.get("results", [])
    total = 0
    hallucinated = 0
    if isinstance(samples, list):
        for sample in samples:
            if isinstance(sample, dict) and sample.get("empty_reference") is True:
                total += 1
                if sample.get("hallucinated") is True:
                    hallucinated += 1
    return hallucinated / total if total else None


def evaluate_gate(report: dict[str, object], thresholds: GateThresholds) -> GateVerdict:
    """Evaluate a benchmark report against frozen thresholds.

    Only languages inside the tier's scope are judged: the Chinese-only Lite
    tier is never failed by English samples (routing, not recognition, keeps
    non-Chinese input away from it — that contract has its own routing tests),
    while a multilingual Standard tier fails closed when a covered language was
    not measured at all. Every other metric with zero contributing samples also
    fails closed: an empty or malformed report can never look like a passing run.
    """
    items: list[GateItem] = []

    if "zh" in thresholds.languages:
        zh_mean, _, zh_count = _aggregate_language(report, "cer", {"zh"})
        if zh_count:
            items.append(
                GateItem(
                    "chinese_cer",
                    zh_mean <= thresholds.max_cer,
                    f"mean CER {zh_mean:.4f} over {zh_count} zh samples"
                    f" (threshold <= {thresholds.max_cer})",
                )
            )
        else:
            items.append(GateItem("chinese_cer", False, "no zh samples measured"))

    if "en" in thresholds.languages:
        en_mean, _, en_count = _aggregate_language(report, "wer", {"en"})
        if en_count:
            items.append(
                GateItem(
                    "english_wer",
                    en_mean <= thresholds.max_wer,
                    f"mean WER {en_mean:.4f} over {en_count} en samples"
                    f" (threshold <= {thresholds.max_wer})",
                )
            )
        else:
            items.append(
                GateItem("english_wer", False, "no en samples measured (required)")
            )

    recall_mean, _, recall_count = _aggregate_entities(report)
    if recall_count:
        items.append(
            GateItem(
                "entity_recall",
                recall_mean >= thresholds.min_entity_recall,
                f"mean entity recall {recall_mean:.4f} over {recall_count} samples"
                f" (threshold >= {thresholds.min_entity_recall})",
            )
        )
    else:
        items.append(GateItem("entity_recall", False, "no samples declared entities"))

    rtf_mean, rtf_worst, rtf_count = _aggregate(report, "rtf")
    if rtf_count:
        items.append(
            GateItem(
                "rtf",
                rtf_mean <= thresholds.max_rtf,
                f"mean RTF {rtf_mean:.4f} (worst {rtf_worst:.4f}) over {rtf_count} samples"
                f" (threshold mean <= {thresholds.max_rtf})",
            )
        )
    else:
        items.append(GateItem("rtf", False, "no samples measured timing"))

    halluc = _hallucination_rate(report)
    if halluc is not None:
        items.append(
            GateItem(
                "hallucination_rate",
                halluc <= thresholds.max_hallucination_rate,
                f"{halluc:.4f} of empty-reference samples produced text"
                f" (threshold <= {thresholds.max_hallucination_rate})",
            )
        )
    else:
        items.append(
            GateItem(
                "hallucination_rate", False, "no empty-reference samples measured"
            )
        )

    return GateVerdict(passed=all(item.passed for item in items), items=tuple(items))


def _aggregate_language(
    report: dict[str, object], key: str, languages: set[str]
) -> tuple[float, float, int]:
    values: list[float] = []
    worst = 0.0
    for sample in _result_dicts(report):
        if str(sample.get("language", "")) not in languages:
            continue
        if sample.get("empty_reference") is True:
            continue  # scored by hallucination rate, not CER/WER
        value = sample.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
            worst = max(worst, float(value))
    if not values:
        return (0.0, 0.0, 0)
    return (sum(values) / len(values), worst, len(values))
