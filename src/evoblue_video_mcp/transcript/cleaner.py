"""Clean raw platform transcripts into normalized text."""

import html
import re

from evoblue_video_mcp.platforms.models import Transcript, TranscriptSegment

_NOISE_MARKER_RE = re.compile(r"\[(Music|Applause|Laughter)\s*\]", re.IGNORECASE)


def clean_transcript(transcript: Transcript) -> Transcript:
    """Return a copy with noise removed, whitespace normalized, and duplicates dropped."""
    return Transcript(
        segments=clean_segments(transcript.segments),
        language=transcript.language,
        source=transcript.source,
    )


def clean_segments(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """Normalize a list of segments, dropping empty and consecutive-duplicate text."""
    cleaned: list[TranscriptSegment] = []
    for segment in segments:
        text = _clean_text(segment.text)
        if not text:
            continue
        if cleaned and cleaned[-1].text == text:
            continue
        cleaned.append(TranscriptSegment(start=segment.start, end=segment.end, text=text))
    return cleaned


def _clean_text(text: str) -> str:
    unescaped = html.unescape(text)
    without_noise = _NOISE_MARKER_RE.sub("", unescaped)
    return " ".join(without_noise.split())
