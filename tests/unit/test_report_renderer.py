"""Markdown renderer emits Schema v1 frontmatter and sections."""

from datetime import UTC, datetime

from evoblue_video_mcp.reports.renderer import render_markdown
from evoblue_video_mcp.reports.schema import ReportDocument


def _doc() -> ReportDocument:
    return ReportDocument(
        analysis_id="a1",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        platform="youtube",
        video_id="dQw4w9WgXcQ",
        title="Test Video",
        author="Author",
        analyzed_at=datetime(2026, 1, 1, tzinfo=UTC),
        summary_mode="auto",
        core_summary="A summary.",
        key_takeaways=["takeaway 1", "takeaway 2"],
    )


def test_render_includes_frontmatter() -> None:
    md = render_markdown(_doc())
    assert md.startswith("---\n")
    assert "schema_version: 1" in md
    assert 'analysis_id: "a1"' in md
    assert 'platform: "youtube"' in md
    assert 'video_id: "dQw4w9WgXcQ"' in md


def test_render_includes_sections() -> None:
    md = render_markdown(_doc())
    assert "# Test Video" in md
    assert "## 核心摘要" in md
    assert "A summary." in md
    assert "## 核心收获" in md
    assert "- takeaway 1" in md
    assert "- takeaway 2" in md
    assert "## 原始链接" in md


def test_render_omits_empty_sections_and_transcript() -> None:
    md = render_markdown(_doc())
    assert "## 完整字幕" not in md
    assert "## 评论风向" not in md
