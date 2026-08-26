"""Report writer: atomic write and user-edit conflict protection."""

import hashlib

import pytest

from evoblue_video_mcp.reports.writer import (
    ReportConflictError,
    ReportWriter,
    report_filename,
    safe_filename,
)


def test_safe_filename_removes_invalid_chars() -> None:
    assert safe_filename("Hello: World?", "a1") == "Hello World.md"
    assert safe_filename("", "a1") == "analysis-a1.md"


async def test_write_report_atomic(tmp_path) -> None:
    writer = ReportWriter(tmp_path)
    result = await writer.write_report("report.md", "content")
    assert result.conflict is False
    assert (tmp_path / "report.md").read_text() == "content"
    assert result.content_hash == hashlib.sha256(b"content").hexdigest()
    assert not (tmp_path / "report.md.tmp").exists()


async def test_write_report_detects_user_edit(tmp_path) -> None:
    writer = ReportWriter(tmp_path)
    first = await writer.write_report("report.md", "v1")
    (tmp_path / "report.md").write_text("user edit")

    second = await writer.write_report("report.md", "v2", previous_hash=first.content_hash)

    assert second.conflict is True
    assert second.path != str(tmp_path / "report.md")
    assert (tmp_path / "report.md").read_text() == "user edit"


async def test_write_report_no_conflict_when_unchanged(tmp_path) -> None:
    writer = ReportWriter(tmp_path)
    first = await writer.write_report("report.md", "v1")
    second = await writer.write_report("report.md", "v1", previous_hash=first.content_hash)
    assert second.conflict is False


async def test_write_report_refuses_overwrite_without_hash(tmp_path) -> None:
    writer = ReportWriter(tmp_path)
    await writer.write_report("report.md", "v1")
    with pytest.raises(ReportConflictError):
        await writer.write_report("report.md", "v2")


def test_report_filename_is_content_addressed() -> None:
    a = report_filename("Title", "a1", "0" * 64)
    b = report_filename("Title", "a1", "1" * 64)
    assert a != b


def test_safe_filename_escapes_windows_reserved_names() -> None:
    assert safe_filename("CON", "a1") != "CON.md"
    assert safe_filename("nul", "a1") != "nul.md"
