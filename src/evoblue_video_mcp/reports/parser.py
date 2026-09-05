"""Parse Markdown Schema v1 reports back into structured data.

Contract: docs/MARKDOWN_SCHEMA.md §2/§3 — the v1 serialization is frozen
(renderer.py): a flat ``key: value`` frontmatter whose scalars are JSON-encoded,
then ``# title`` and fixed-order ``##`` sections. This reader implements exactly
that frozen shape: strict, dependency free, deliberately intolerant. Any
deviation (arbitrary YAML, missing frontmatter, unknown ``schema_version``,
naive datetimes) raises a :class:`MarkdownParseError` carrying the frozen
diagnostic code — quarantine material for the index rebuild, never a guess.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from urllib.parse import urlsplit

MD_FRONTMATTER_INVALID = "MD_FRONTMATTER_INVALID"
MD_UNKNOWN_SCHEMA_VERSION = "MD_UNKNOWN_SCHEMA_VERSION"
MD_MISSING_FIELD = "MD_MISSING_FIELD"
MD_BODY_STRUCTURE_INVALID = "MD_BODY_STRUCTURE_INVALID"
MD_ENCODING_ERROR = "MD_ENCODING_ERROR"
MD_EMPTY_BODY = "MD_EMPTY_BODY"  # warning severity: indexed, but recorded

_KNOWN_SCHEMA_VERSIONS = (1,)
_SUMMARY_MODES = ("auto", "standard", "unboxing")
_REQUIRED_FIELDS = (
    "analysis_id",
    "source_url",
    "platform",
    "video_id",
    "title",
    "analyzed_at",
)
# §3 headings in frozen emission order; content between headings is the section.
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("## 核心摘要", "core_summary"),
    ("## 核心收获", "key_takeaways"),
    ("## 时间轴大纲", "timeline_outline"),
    ("## 内容分析", "content_analysis"),
    ("## 评论风向", "comment_sentiment"),
    ("## 视频信息", "video_information"),
    ("## 原始链接", "_link"),  # always present; source_url in frontmatter is authoritative
    ("## 完整字幕", "transcript"),
)
_ANALYSIS_SECTIONS = (
    "core_summary",
    "key_takeaways",
    "timeline_outline",
    "content_analysis",
    "comment_sentiment",
    "video_information",
)


class MarkdownParseError(ValueError):
    """A report violates the frozen Schema v1 shape; ``code`` is the diagnostic."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ParsedReport:
    """The structured form of one Schema v1 report file."""

    schema_version: int
    analysis_id: str
    source_url: str
    platform: str
    video_id: str
    title: str
    author: str
    published_at: datetime | None
    analyzed_at: datetime
    summary_mode: str
    language: str
    asr_provider: str
    asr_model: str
    asr_model_version: str
    tags: tuple[str, ...]
    core_summary: str
    key_takeaways: tuple[str, ...]
    timeline_outline: str
    content_analysis: str
    comment_sentiment: str
    video_information: str
    transcript: str | None
    has_analysis_content: bool


def frontmatter_block(text: str) -> str:
    """Return the raw ``---`` delimited frontmatter block, or ``""`` if absent."""
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---\n", 4)
    if end < 0:
        return ""
    return text[: end + 5]


def decode_report_bytes(data: bytes) -> ParsedReport:
    """Decode bytes (UTF-8, no BOM — §1) and parse; encoding issues are fatal.

    Read-side tolerance: CRLF (the common Windows-editor save form) is
    normalized to LF before parsing; generated files remain LF-only.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        raise MarkdownParseError(MD_ENCODING_ERROR, "report has a UTF-8 BOM")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MarkdownParseError(
            MD_ENCODING_ERROR, f"report is not valid UTF-8: {exc}"
        ) from exc
    return parse_markdown_report(text.replace("\r\n", "\n").replace("\r", "\n"))


def parse_markdown_report(text: str) -> ParsedReport:
    """Parse one Schema v1 report; raise :class:`MarkdownParseError` on violation."""
    if not text.startswith("---\n"):
        raise MarkdownParseError(MD_FRONTMATTER_INVALID, "missing frontmatter opener")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise MarkdownParseError(MD_FRONTMATTER_INVALID, "missing frontmatter closer")
    fields = _parse_frontmatter(text[4:end])
    body = text[end + 5 :]
    sections, present = _parse_sections(body)

    raw_version = fields.get("schema_version")
    try:
        schema_version = int(raw_version)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise MarkdownParseError(
            MD_UNKNOWN_SCHEMA_VERSION, f"schema_version missing or invalid: {raw_version!r}"
        ) from exc
    if schema_version not in _KNOWN_SCHEMA_VERSIONS:
        raise MarkdownParseError(
            MD_UNKNOWN_SCHEMA_VERSION, f"unknown schema_version: {schema_version}"
        )

    values: dict[str, str] = {}
    for key in (
        "analysis_id",
        "source_url",
        "platform",
        "video_id",
        "title",
        "summary_mode",
        "author",
        "language",
        "asr_provider",
        "asr_model",
        "asr_model_version",
    ):
        values[key] = _json_str(fields, key)
    for key in _REQUIRED_FIELDS:
        if key != "analyzed_at" and not values[key]:
            raise MarkdownParseError(MD_MISSING_FIELD, f"required field missing: {key}")
    parsed_url = urlsplit(values["source_url"])
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise MarkdownParseError(
            MD_MISSING_FIELD,
            f"source_url must be an absolute HTTP(S) URL, got {values['source_url']!r}",
        )
    if values["platform"] != values["platform"].lower():
        raise MarkdownParseError(
            MD_MISSING_FIELD,
            f"platform must be lowercase, got {values['platform']!r}",
        )
    # §3 必现锚点 (frozen via INDEX_REBUILD §3 MD_BODY_STRUCTURE_INVALID): the
    # body must open with '# {title}' and contain the '## 原始链接' section.
    first_body_line = next((line for line in body.split("\n") if line.strip()), "")
    if first_body_line != f"# {values['title']}":
        raise MarkdownParseError(
            MD_BODY_STRUCTURE_INVALID,
            f"missing or mismatched '# {{title}}' heading: {first_body_line!r}",
        )
    if "_link" not in present:
        raise MarkdownParseError(
            MD_BODY_STRUCTURE_INVALID, "missing required '## 原始链接' section"
        )
    if not values["summary_mode"]:
        raise MarkdownParseError(MD_MISSING_FIELD, "required field missing: summary_mode")
    if values["summary_mode"] not in _SUMMARY_MODES:
        raise MarkdownParseError(
            MD_MISSING_FIELD,
            f"summary_mode must be one of {_SUMMARY_MODES}, got {values['summary_mode']!r}",
        )

    tags = _json_tags(fields)
    published_at = _json_datetime(fields, "published_at", required=False)
    analyzed_at = cast(datetime, _json_datetime(fields, "analyzed_at", required=True))

    core_summary = sections["core_summary"]
    takeaways_raw = sections["key_takeaways"]
    key_takeaways = tuple(
        line[2:].strip() for line in takeaways_raw.split("\n") if line.startswith("- ")
    )
    transcript = sections["transcript"] or None
    has_analysis_content = any(sections[name] for name in _ANALYSIS_SECTIONS)

    return ParsedReport(
        schema_version=schema_version,
        analysis_id=values["analysis_id"],
        source_url=values["source_url"],
        platform=values["platform"],
        video_id=values["video_id"],
        title=values["title"],
        author=values["author"],
        published_at=published_at,
        analyzed_at=analyzed_at,
        summary_mode=values["summary_mode"],
        language=values["language"],
        asr_provider=values["asr_provider"],
        asr_model=values["asr_model"],
        asr_model_version=values["asr_model_version"],
        tags=tags,
        core_summary=core_summary,
        key_takeaways=key_takeaways,
        timeline_outline=sections["timeline_outline"],
        content_analysis=sections["content_analysis"],
        comment_sentiment=sections["comment_sentiment"],
        video_information=sections["video_information"],
        transcript=transcript,
        has_analysis_content=has_analysis_content,
    )


def _parse_frontmatter(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in block.split("\n"):
        if not line.strip():
            continue
        key, sep, raw = line.partition(": ")
        if not sep:
            if line.endswith(":"):
                # ``key:`` (null scalar) is treated exactly like an absent key;
                # required-field checks decide the diagnostic.
                fields[line[:-1].strip()] = ""
                continue
            raise MarkdownParseError(
                MD_FRONTMATTER_INVALID, f"malformed frontmatter line: {line!r}"
            )
        fields[key.strip()] = raw
    return fields


def _parse_sections(body: str) -> tuple[dict[str, str], set[str]]:
    """Split the body on exact frozen ``## `` headings (§3 解析规则).

    Returns the section contents and the set of headings actually present.
    """
    sections: dict[str, str] = {attr: "" for _, attr in _SECTIONS}
    present: set[str] = set()
    current: str | None = None
    chunks: dict[str, list[str]] = {}
    for line in body.split("\n"):
        matched: str | None = None
        for heading, attr in _SECTIONS:
            if line == heading:
                matched = attr
                break
        if matched is not None:
            current = matched
            present.add(current)
            chunks[current] = []
        elif current is not None:
            chunks[current].append(line)
    for attr, lines in chunks.items():
        sections[attr] = "\n".join(lines).strip()
    return sections, present


def _json_str(fields: dict[str, str], key: str) -> str:
    raw = fields.get(key)
    if raw is None or raw == "":
        return ""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MarkdownParseError(
            MD_FRONTMATTER_INVALID, f"frontmatter {key} is not a JSON scalar: {raw!r}"
        ) from exc
    if not isinstance(value, str):
        raise MarkdownParseError(
            MD_FRONTMATTER_INVALID, f"frontmatter {key} must be a string, got {value!r}"
        )
    return value


def _json_tags(fields: dict[str, str]) -> tuple[str, ...]:
    raw = fields.get("tags")
    if raw is None or raw == "":
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MarkdownParseError(
            MD_FRONTMATTER_INVALID, f"frontmatter tags is not a JSON array: {raw!r}"
        ) from exc
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise MarkdownParseError(
            MD_FRONTMATTER_INVALID, "frontmatter tags must be a JSON array of strings"
        )
    return tuple(value)


def _json_datetime(fields: dict[str, str], key: str, *, required: bool) -> datetime | None:
    raw = fields.get(key)
    if raw is None or raw in ("null", ""):
        if required:
            raise MarkdownParseError(MD_MISSING_FIELD, f"required field missing: {key}")
        return None
    # Both RFC 3339 spellings occur in frozen v1 output: bare isoformat (valid
    # YAML timestamp, renderer's published_at) and JSON-quoted (analyzed_at).
    text = raw
    try:
        value_json = json.loads(raw)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(value_json, str):
            text = value_json
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MarkdownParseError(
            MD_MISSING_FIELD, f"frontmatter {key} is not a valid RFC 3339 datetime"
        ) from exc
    if value.tzinfo is None or value.utcoffset() is None:
        # MARKDOWN_SCHEMA §2 时区强制: naive datetimes are rejected on the read
        # side with the same severity as a missing field.
        raise MarkdownParseError(
            MD_MISSING_FIELD, f"frontmatter {key} must be timezone-aware"
        )
    return value
