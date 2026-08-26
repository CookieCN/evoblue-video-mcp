"""Transcript cleaner normalizes noise, entities, whitespace, and duplicates."""

from evoblue_video_mcp.platforms.models import Transcript, TranscriptSegment
from evoblue_video_mcp.transcript.cleaner import clean_transcript


def _t(*texts: str) -> Transcript:
    return Transcript(
        segments=[
            TranscriptSegment(start=float(i * 2), end=float(i * 2 + 2), text=t)
            for i, t in enumerate(texts)
        ]
    )


def test_removes_noise_markers() -> None:
    cleaned = clean_transcript(_t("[Music]", "Hello world", "[Applause]"))
    assert [s.text for s in cleaned.segments] == ["Hello world"]


def test_unescapes_html_entities() -> None:
    cleaned = clean_transcript(_t("Tom &amp; Jerry"))
    assert cleaned.segments[0].text == "Tom & Jerry"


def test_normalizes_whitespace() -> None:
    cleaned = clean_transcript(_t("  hello   world  "))
    assert cleaned.segments[0].text == "hello world"


def test_drops_empty_and_duplicate_segments() -> None:
    cleaned = clean_transcript(_t("hello", "hello", "   ", "world"))
    assert [s.text for s in cleaned.segments] == ["hello", "world"]
