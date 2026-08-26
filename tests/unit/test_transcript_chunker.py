"""Chunker is deterministic and respects the max-chars boundary."""

import pytest

from evoblue_video_mcp.transcript.chunker import chunk_text


def test_chunk_text_is_deterministic() -> None:
    text = "one two three four five six"
    assert chunk_text(text, 10) == chunk_text(text, 10)


def test_chunk_text_respects_boundary() -> None:
    text = " ".join(["word"] * 100)
    chunks = chunk_text(text, 20)
    assert len(chunks) > 1
    assert all(len(chunk) <= 20 for chunk in chunks)


def test_chunk_text_splits_long_unbroken_word() -> None:
    text = "a" * 50
    chunks = chunk_text(text, 10)
    assert len(chunks) == 5
    assert all(len(chunk) == 10 for chunk in chunks)


def test_chunk_text_empty() -> None:
    assert chunk_text("", 10) == []


def test_chunk_text_rejects_non_positive_max_chars() -> None:
    with pytest.raises(ValueError):
        chunk_text("hello", 0)
