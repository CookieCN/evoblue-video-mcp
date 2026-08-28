"""Formal default-model approvals (ASR_PLAN section 7 release gate).

A tier may be presented as a formal default only when every gate line is
recorded: the benchmark verdict against frozen thresholds, the license and
redistribution review, the pinned manifest, and the verified download path.
Approval is keyed by exact ``(model_id, version)`` so a republished model or a
bumped version starts unapproved again — the gate must be re-run, not inherited.

Recorded 2026-08-28 from ``benchmarks/results/`` (TTS corpus v1, frozen
thresholds v1, Windows 11 x86-64, 12 logical CPUs); full evidence and the
failure analysis live in ``docs/ASR_RELEASE_GATE.md``.

- **Standard** SenseVoiceSmall INT8: benchmark PASS (zh CER 0.0, en WER 0.077,
  entity recall 1.0, RTF 0.06) and a recorded license review (FunASR Model
  License 1.1, upstream-only distribution). Approved as the formal Standard
  default and the automatic zh/mixed-language recommendation.
- **Lite** Zipformer CTC small INT8: benchmark FAIL (entity recall 0.50 <
  0.60 — English brand names and mixed-language entities) and its exact
  archive carries no license file. NOT approved; it stays installable and
  explicitly selectable, never the automatic recommendation, matching the
  ASR_PLAN failure policy for a Lite quality failure.
- **whisper.cpp Base**: license is clean (MIT), but no benchmark has been run
  (no pinned CLI runtime on the gate machine). NOT approved as a formal
  default; its routing role remains the documented out-of-coverage fallback.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelApproval:
    """One recorded release-gate approval (or its absence, by not being here)."""

    tier: str
    approved: bool
    thresholds_version: str
    evidence: str
    reason: str


_FORMAL_DEFAULTS: dict[tuple[str, str], ModelApproval] = {
    ("sensevoice-small-int8", "2024-07-17"): ModelApproval(
        tier="standard",
        approved=True,
        thresholds_version="v1",
        evidence="benchmarks/results/standard-v1.json",
        reason=(
            "Benchmark gate v1 PASS (zh CER 0.0, en WER 0.077, entity recall 1.0, "
            "RTF 0.06); FunASR Model License 1.1 review recorded; upstream-only "
            "manifest pinned and download path verified."
        ),
    ),
    ("zipformer-ctc-small-zh-int8", "2025-07-16"): ModelApproval(
        tier="lite",
        approved=False,
        thresholds_version="v1",
        evidence="benchmarks/results/lite-v1.json",
        reason=(
            "Benchmark gate v1 FAIL (entity recall 0.50 < 0.60: English brand "
            "names in zh/mixed text); archive carries no license file. Remains "
            "installable and explicitly selectable, never the auto recommendation."
        ),
    ),
    ("whisper-cpp-base", "80da2d8"): ModelApproval(
        tier="multilingual",
        approved=False,
        thresholds_version="v1",
        evidence="docs/ASR_RELEASE_GATE.md (no benchmark run: no pinned CLI runtime)",
        reason=(
            "MIT license is clean, but the ASR_PLAN defines no fallback gate and "
            "no benchmark has been measured; stays the out-of-coverage fallback "
            "recommendation, not a formal default."
        ),
    ),
}


def get_approval(model_id: str, version: str) -> ModelApproval | None:
    """Return the recorded approval for an exact model version, if any."""
    return _FORMAL_DEFAULTS.get((model_id, version))


def is_formal_default(model_id: str, version: str) -> bool:
    """True only when this exact model version passed the recorded release gate."""
    approval = _FORMAL_DEFAULTS.get((model_id, version))
    return approval is not None and approval.approved
