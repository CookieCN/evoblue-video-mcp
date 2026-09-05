"""Markdown Schema v1 parser: frozen parse rules and renderer round-trip."""

from datetime import UTC, datetime

import pytest

from evoblue_video_mcp.reports.parser import (
    MD_BODY_STRUCTURE_INVALID,
    MD_ENCODING_ERROR,
    MD_FRONTMATTER_INVALID,
    MD_MISSING_FIELD,
    MD_UNKNOWN_SCHEMA_VERSION,
    MarkdownParseError,
    decode_report_bytes,
    frontmatter_block,
    parse_markdown_report,
)
from evoblue_video_mcp.reports.renderer import render_markdown
from evoblue_video_mcp.reports.schema import ReportDocument


def _doc(**overrides: object) -> ReportDocument:
    kwargs: dict[str, object] = {
        "analysis_id": "job-1",
        "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "platform": "youtube",
        "video_id": "dQw4w9WgXcQ",
        "title": "跨境电商选品指南",
        "author": "Wilson",
        "published_at": datetime(2025, 12, 31, tzinfo=UTC),
        "analyzed_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        "summary_mode": "auto",
        "language": "zh",
        "asr_provider": "",
        "asr_model": "",
        "asr_model_version": "",
        "tags": ["选品", "跨境电商"],
        "core_summary": "本期讲选品方法论。",
        "key_takeaways": ["第一点", "第二点"],
        "timeline_outline": "00:00 开场",
        "content_analysis": "分析正文。",
        "transcript": "大家好今天讲选品策略。",
    }
    kwargs.update(overrides)
    return ReportDocument(**kwargs)  # type: ignore[arg-type]


def test_renderer_output_round_trips_through_parser() -> None:
    doc = _doc()
    parsed = parse_markdown_report(render_markdown(doc))
    assert parsed.analysis_id == "job-1"
    assert parsed.platform == "youtube"
    assert parsed.video_id == "dQw4w9WgXcQ"
    assert parsed.title == "跨境电商选品指南"
    assert parsed.author == "Wilson"
    assert parsed.published_at == datetime(2025, 12, 31, tzinfo=UTC)
    assert parsed.analyzed_at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert parsed.summary_mode == "auto"
    assert parsed.language == "zh"
    assert parsed.tags == ("选品", "跨境电商")
    assert parsed.core_summary == "本期讲选品方法论。"
    assert parsed.key_takeaways == ("第一点", "第二点")
    assert parsed.timeline_outline == "00:00 开场"
    assert parsed.content_analysis == "分析正文。"
    assert parsed.transcript == "大家好今天讲选品策略。"
    assert parsed.has_analysis_content


def test_minimal_report_parses_with_empty_optionals() -> None:
    doc = _doc(
        author="",
        published_at=None,
        tags=[],
        core_summary="",
        key_takeaways=[],
        timeline_outline="",
        content_analysis="",
        transcript=None,
    )
    md = render_markdown(doc)
    parsed = parse_markdown_report(md)
    assert parsed.author == ""
    assert parsed.published_at is None
    assert parsed.tags == ()
    assert parsed.core_summary == ""
    assert parsed.transcript is None
    assert parsed.has_analysis_content is False  # MD_EMPTY_BODY is the caller's call


def test_utf8_bom_is_encoding_error() -> None:
    data = b"\xef\xbb\xbf" + render_markdown(_doc()).encode("utf-8")
    with pytest.raises(MarkdownParseError) as excinfo:
        decode_report_bytes(data)
    assert excinfo.value.code == MD_ENCODING_ERROR


def test_crlf_files_are_tolerated_on_read() -> None:
    """MARKDOWN_SCHEMA §1: generation is LF-only, but Windows editors save
    CRLF — the read side normalizes instead of quarantining."""
    crlf = render_markdown(_doc()).replace("\n", "\r\n").encode("utf-8")
    parsed = decode_report_bytes(crlf)
    assert parsed.analysis_id == "job-1"
    assert parsed.core_summary == "本期讲选品方法论。"


def test_invalid_utf8_is_encoding_error() -> None:
    with pytest.raises(MarkdownParseError) as excinfo:
        decode_report_bytes(b"\xff\xfe\x00bad")
    assert excinfo.value.code == MD_ENCODING_ERROR


def test_missing_frontmatter_is_invalid() -> None:
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report("# no frontmatter\n")
    assert excinfo.value.code == MD_FRONTMATTER_INVALID


def test_unclosed_frontmatter_is_invalid() -> None:
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report("---\nanalysis_id: \"a1\"\n")
    assert excinfo.value.code == MD_FRONTMATTER_INVALID


def test_unknown_schema_version_is_rejected() -> None:
    md = render_markdown(_doc()).replace("schema_version: 1", "schema_version: 2", 1)
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report(md)
    assert excinfo.value.code == MD_UNKNOWN_SCHEMA_VERSION


def test_missing_required_field_is_rejected() -> None:
    md = render_markdown(_doc()).replace('platform: "youtube"\n', "", 1)
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report(md)
    assert excinfo.value.code == MD_MISSING_FIELD


def test_naive_datetime_is_rejected_like_missing_field() -> None:
    md = render_markdown(_doc()).replace(
        'analyzed_at: "2026-01-01T12:00:00+00:00"',
        'analyzed_at: "2026-01-01T12:00:00"',
        1,
    )
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report(md)
    assert excinfo.value.code == MD_MISSING_FIELD


def test_off_enum_summary_mode_is_rejected() -> None:
    md = render_markdown(_doc()).replace('summary_mode: "auto"', 'summary_mode: "vlog"', 1)
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report(md)
    assert excinfo.value.code == MD_MISSING_FIELD


def test_empty_summary_mode_is_rejected() -> None:
    md = render_markdown(_doc()).replace('summary_mode: "auto"', "summary_mode:", 1)
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report(md)
    assert excinfo.value.code == MD_MISSING_FIELD


def test_missing_h1_anchor_is_body_structure_error() -> None:
    md = render_markdown(_doc())
    body_without_h1 = "\n".join(
        line for line in md.split("\n") if line != "# 跨境电商选品指南"
    )
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report(body_without_h1)
    assert excinfo.value.code == MD_BODY_STRUCTURE_INVALID


def test_missing_link_section_is_body_structure_error() -> None:
    md = render_markdown(_doc())
    stripped = "\n".join(line for line in md.split("\n") if line != "## 原始链接")
    # The url content line now merges into the previous section, but the frozen
    # '## 原始链接' anchor itself is gone → body structure violation.
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report(stripped)
    assert excinfo.value.code == MD_BODY_STRUCTURE_INVALID


def test_non_http_source_url_is_rejected() -> None:
    md = render_markdown(_doc()).replace(
        'source_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"',
        'source_url: "ftp://example.com/video"',
        1,
    )
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report(md)
    assert excinfo.value.code == MD_MISSING_FIELD


def test_uppercase_platform_is_rejected() -> None:
    md = render_markdown(_doc()).replace('platform: "youtube"', 'platform: "YouTube"', 1)
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report(md)
    assert excinfo.value.code == MD_MISSING_FIELD


def test_unknown_frontmatter_keys_are_ignored() -> None:
    md = render_markdown(_doc()).replace(
        "---\n", "---\nfuture_field: \"whatever\"\n", 1
    )
    assert parse_markdown_report(md).analysis_id == "job-1"


def test_non_yaml_json_scalar_is_frontmatter_invalid() -> None:
    md = render_markdown(_doc()).replace('title: "跨境电商选品指南"', "title: 跨境电商选品指南", 1)
    with pytest.raises(MarkdownParseError) as excinfo:
        parse_markdown_report(md)
    assert excinfo.value.code == MD_FRONTMATTER_INVALID


def test_sections_split_on_exact_headings_only() -> None:
    # A "## 核心摘要" mention inside content must not split the section.
    doc = _doc(content_analysis="前文提到 ## 核心摘要 字样不算标题")
    parsed = parse_markdown_report(render_markdown(doc))
    assert parsed.content_analysis == "前文提到 ## 核心摘要 字样不算标题"


def test_frontmatter_block_extraction() -> None:
    md = render_markdown(_doc())
    block = frontmatter_block(md)
    assert block.startswith("---\n")
    assert block.endswith("---\n")
    assert "analysis_id" in block
    assert frontmatter_block("# no fm\n") == ""
