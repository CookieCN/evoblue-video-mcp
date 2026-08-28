"""Deterministic scoring metrics for ASR benchmark output."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

# Fullwidth CJK punctuation is intentionally literal here (it is data to strip).
_PUNCT = re.compile(r"[\s，。！？、；：\"'“”‘’（）\(\)\[\]【】,\.!\?;:]+")  # noqa: RUF001

# Scoring keeps CJK ideographs, kana, hangul, Latin letters and digits; every
# other codepoint (punctuation, symbols, whitespace) is dropped so a missing
# sentence-final period is not scored as a character error.
_SCORING_KEEP = re.compile(r"[^\w一-鿿぀-ヿ가-힯]+", re.UNICODE)

# Word scoring keeps single spaces between tokens instead: WER compares
# whitespace tokens, so collapsing all whitespace would turn every transcript
# into one giant token and score 1.0 regardless of content.
_SCORING_WORD = re.compile(r"[^\w一-鿿぀-ヿ가-힯]+", re.UNICODE)


def normalize_for_scoring(text: str) -> str:
    """Normalize transcript text for CER scoring.

    NFKC folds fullwidth Latin/digits into ASCII; lowercasing unifies case;
    punctuation and whitespace removal matches mainstream CER practice where
    scores are computed on characters, not typography.
    """
    folded = unicodedata.normalize("NFKC", text)
    return _SCORING_KEEP.sub("", folded).lower()


def normalize_words(text: str) -> str:
    """Normalize transcript text for WER scoring, preserving token boundaries."""
    folded = unicodedata.normalize("NFKC", text)
    return _SCORING_WORD.sub(" ", folded).lower().strip()



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


def entity_recall(entities: Sequence[str], hypothesis: str) -> float:
    """Fraction of reference named entities that appear verbatim in the output.

    Comparison strips whitespace/punctuation and lowercases Latin letters, so a
    brand name survives surrounding punctuation but a mis-recognized character
    still counts as a miss. An empty entity list scores 0.0 with count 0 —
    callers decide whether that means "not measured" rather than a perfect or
    failing score.
    """
    if not entities:
        return 0.0
    normalized = _PUNCT.sub("", hypothesis).lower()
    hits = sum(1 for entity in entities if _PUNCT.sub("", entity).lower() in normalized)
    return hits / len(entities)
