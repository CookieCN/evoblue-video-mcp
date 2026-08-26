"""Deterministic chunking of transcript text for LLM processing."""

from evoblue_video_mcp.platforms.models import Transcript


def chunk_transcript(transcript: Transcript, max_chars: int) -> list[str]:
    """Split a transcript into bounded text chunks, preserving order."""
    full_text = " ".join(segment.text for segment in transcript.segments)
    return chunk_text(full_text, max_chars)


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split ``text`` into chunks no longer than ``max_chars`` at word boundaries.

    The split is deterministic: the same input always yields the same chunks.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    remaining = text.strip()
    chunks: list[str] = []
    while len(remaining) > max_chars:
        split_at = remaining.rfind(" ", 0, max_chars)
        if split_at <= 0:
            split_at = max_chars
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks
