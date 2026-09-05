"""Canonical in-memory representation of Markdown Schema v1."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class ReportDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    analysis_id: str = Field(min_length=1)
    source_url: HttpUrl
    platform: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str = ""
    published_at: datetime | None = None
    analyzed_at: datetime
    summary_mode: Literal["auto", "standard", "unboxing"]
    language: str = ""
    asr_provider: str = ""
    asr_model: str = ""
    asr_model_version: str = ""
    tags: list[str] = Field(default_factory=list)
    core_summary: str = ""
    key_takeaways: list[str] = Field(default_factory=list)
    timeline_outline: str = ""
    content_analysis: str = ""
    comment_sentiment: str = ""
    video_information: str = ""
    transcript: str | None = None

    @field_validator("published_at", "analyzed_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        """Markdown Schema v1 mandates tz-aware datetimes (see docs/MARKDOWN_SCHEMA.md)."""
        if value is not None and value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware (RFC 3339 with offset)")
        return value
