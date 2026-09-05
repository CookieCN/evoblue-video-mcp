"""Markdown Schema v1 datetime contract: timezone awareness is mandatory.

Regression guard for docs/MARKDOWN_SCHEMA.md §2 (时区强制): naive datetimes
cannot be anchored to an epoch, which would make history ordering
unreproducible after a delete-SQLite rebuild.
"""

from datetime import UTC, datetime, timedelta, tzinfo

import pytest
from pydantic import ValidationError

from evoblue_video_mcp.reports.schema import ReportDocument


def _doc_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "analysis_id": "a1",
        "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "platform": "youtube",
        "video_id": "dQw4w9WgXcQ",
        "title": "Test Video",
        "analyzed_at": datetime(2026, 1, 1, tzinfo=UTC),
        "summary_mode": "auto",
    }
    kwargs.update(overrides)
    return kwargs


class _BrokenTz(tzinfo):
    """A tzinfo whose utcoffset() is None — naive by Python's definition."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None


def test_tzaware_datetimes_accepted() -> None:
    doc = ReportDocument.model_validate(
        _doc_kwargs(published_at=datetime(2025, 12, 31, tzinfo=UTC))
    )
    assert doc.published_at is not None
    assert doc.published_at.utcoffset() == timedelta(0)


def test_naive_analyzed_at_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ReportDocument.model_validate(
            _doc_kwargs(analyzed_at=datetime(2026, 1, 1))
        )


def test_naive_published_at_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ReportDocument.model_validate(
            _doc_kwargs(published_at=datetime(2025, 12, 31))
        )


def test_utcoffset_none_tzinfo_rejected() -> None:
    """`tzinfo is not None` alone is insufficient — awareness means utcoffset() works."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        ReportDocument.model_validate(
            _doc_kwargs(analyzed_at=datetime(2026, 1, 1, tzinfo=_BrokenTz()))
        )
