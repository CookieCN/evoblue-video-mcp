"""Deterministic scoring metrics for ASR benchmark output."""

from collections.abc import Sequence
from dataclasses import dataclass


def _levenshtein(a: Sequence[str], b: Sequence[str]) -> int:
    """Levenshtein distance over token sequences, using O(min) memory."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        cur = [i]
        for j, y in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    """Chinese-style CER: edit distance over characters divided by reference length.

    An empty reference scores 0.0 only when the hypothesis is also empty;
    otherwise it scores 1.0, so hallucinated output on silence is never rewarded.
    """
    ref = list(reference)
    if not ref:
        return 0.0 if not hypothesis else 1.0
    return _levenshtein(ref, list(hypothesis)) / len(ref)


def word_error_rate(reference: str, hypothesis: str) -> float:
    """English-style WER: edit distance over whitespace tokens divided by reference count.

    An empty reference scores 0.0 only when the hypothesis is also empty;
    otherwise it scores 1.0, so hallucinated output on silence is never rewarded.
    """
    ref = reference.split()
    if not ref:
        return 0.0 if not hypothesis.split() else 1.0
    return _levenshtein(ref, hypothesis.split()) / len(ref)


@dataclass(frozen=True)
class SegmentBoundary:
    """A reference or hypothesis segment's start/end in seconds."""

    start: float
    end: float


def segment_boundary_error(
    reference: Sequence[SegmentBoundary], hypothesis: Sequence[SegmentBoundary]
) -> float:
    """Mean absolute boundary error in seconds, penalizing unmatched segments.

    Segments aligned by position contribute their start/end error directly, and
    the total is divided by the number of boundaries (two per segment), not the
    number of segments. Unmatched segments on either side — dropped or
    hallucinated — each count as two missing boundaries penalized by the
    reference mean segment duration, so they raise the score rather than being
    silently ignored. An empty reference scores 0.0 only when the hypothesis is
    also empty; otherwise it scores 1.0.
    """
    if not reference:
        return 0.0 if not hypothesis else 1.0
    n_ref = len(reference)
    n_hyp = len(hypothesis)

    total = sum(
        abs(r.start - h.start) + abs(r.end - h.end)
        for r, h in zip(reference, hypothesis, strict=False)
    )

    unmatched = abs(n_ref - n_hyp)
    if unmatched:
        mean_duration = sum(seg.end - seg.start for seg in reference) / n_ref
        total += unmatched * 2 * mean_duration

    return total / (2 * max(n_ref, n_hyp))
