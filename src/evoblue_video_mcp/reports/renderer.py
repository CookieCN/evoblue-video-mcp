"""Render a ReportDocument to Markdown Schema v1."""

import json

from evoblue_video_mcp.reports.schema import ReportDocument


def render_markdown(doc: ReportDocument) -> str:
    """Render a report as YAML frontmatter plus the standard sections."""
    return f"{_render_frontmatter(doc)}\n\n{_render_sections(doc)}\n"


def _render_frontmatter(doc: ReportDocument) -> str:
    published = doc.published_at.isoformat() if doc.published_at else "null"
    fields = [
        f"schema_version: {doc.schema_version}",
        f"analysis_id: {_yaml(doc.analysis_id)}",
        f"source_url: {_yaml(str(doc.source_url))}",
        f"platform: {_yaml(doc.platform)}",
        f"video_id: {_yaml(doc.video_id)}",
        f"title: {_yaml(doc.title)}",
        f"author: {_yaml(doc.author)}",
        f"published_at: {published}",
        f"analyzed_at: {_yaml(doc.analyzed_at.isoformat())}",
        f"summary_mode: {_yaml(doc.summary_mode)}",
        f"language: {_yaml(doc.language)}",
        f"tags: {json.dumps(doc.tags, ensure_ascii=False)}",
    ]
    return "---\n" + "\n".join(fields) + "\n---"


def _yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_sections(doc: ReportDocument) -> str:
    sections = [f"# {doc.title}"]
    if doc.core_summary:
        sections.append(f"## 核心摘要\n\n{doc.core_summary}")
    if doc.key_takeaways:
        sections.append("## 核心收获\n\n" + "\n".join(f"- {t}" for t in doc.key_takeaways))
    if doc.timeline_outline:
        sections.append(f"## 时间轴大纲\n\n{doc.timeline_outline}")
    if doc.content_analysis:
        sections.append(f"## 内容分析\n\n{doc.content_analysis}")
    if doc.comment_sentiment:
        sections.append(f"## 评论风向\n\n{doc.comment_sentiment}")
    if doc.video_information:
        sections.append(f"## 视频信息\n\n{doc.video_information}")
    sections.append(f"## 原始链接\n\n{doc.source_url}")
    if doc.transcript:
        sections.append(f"## 完整字幕\n\n{doc.transcript}")
    return "\n\n".join(sections)
