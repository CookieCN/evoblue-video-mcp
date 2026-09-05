"""P3-007 review: the report-root pointer must be crash-safe (§4).

``Path.write_text`` truncates in place; a crash mid-write would leave an
empty or half-written pointer — breaking the very disaster recovery the file
exists to guarantee. EVERY write (setting AND clearing) is therefore temp
file + fsync + atomic ``os.replace`` — clearing writes an empty tombstone
instead of unlinking — with the temp cleaned up on failure, and any failure
PROPAGATING so the settings endpoint can refuse the database commit.
"""

import os
from pathlib import Path

import pytest

from evoblue_video_mcp.storage.rebuild import (
    REPORT_POINTER_FILENAME,
    ReportPointerUnavailable,
    read_report_pointer,
    write_report_pointer,
)


def test_pointer_round_trip_and_no_temp_leftover(tmp_path: Path) -> None:
    assert read_report_pointer(tmp_path) is None
    write_report_pointer(tmp_path, Path("D:/Reports/custom"))
    assert read_report_pointer(tmp_path) == Path("D:/Reports/custom")
    assert list(tmp_path.glob(f"{REPORT_POINTER_FILENAME}.tmp*")) == []
    write_report_pointer(tmp_path, None)  # clearing writes an empty tombstone
    assert read_report_pointer(tmp_path) is None
    pointer = tmp_path / REPORT_POINTER_FILENAME
    assert pointer.exists() and pointer.read_bytes() == b""
    assert list(tmp_path.glob(f"{REPORT_POINTER_FILENAME}.tmp*")) == []


def test_pointer_write_failure_keeps_previous_value_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_report_pointer(tmp_path, Path("C:/old/reports"))
    pointer = tmp_path / REPORT_POINTER_FILENAME
    before = pointer.read_bytes()

    def _boom(src: object, dst: object) -> None:
        raise OSError("simulated crash between write and replace")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        write_report_pointer(tmp_path, Path("C:/new/reports"))
    monkeypatch.undo()
    # The previous pointer survives byte-for-byte; no temp file is stranded.
    assert pointer.read_bytes() == before
    assert read_report_pointer(tmp_path) == Path("C:/old/reports")
    assert list(tmp_path.glob(f"{REPORT_POINTER_FILENAME}.tmp*")) == []


def test_pointer_clear_failure_propagates_never_silently_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P1: a failed CLEAR must raise — a silently swallowed failure
    would let the settings row move to NULL while the stale pointer file
    survives to revive the old directory after a database deletion."""
    write_report_pointer(tmp_path, Path("C:/old/reports"))
    pointer = tmp_path / REPORT_POINTER_FILENAME
    before = pointer.read_bytes()

    def _boom(src: object, dst: object) -> None:
        raise OSError("simulated crash while replacing the tombstone")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        write_report_pointer(tmp_path, None)
    monkeypatch.undo()
    # The old pointer is intact — not half-cleared, no stranded temp.
    assert pointer.read_bytes() == before
    assert read_report_pointer(tmp_path) == Path("C:/old/reports")
    assert list(tmp_path.glob(f"{REPORT_POINTER_FILENAME}.tmp*")) == []


def test_unreadable_pointer_fails_closed_not_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P1: a pointer that EXISTS but cannot be read must be
    distinguishable from "no pointer" — collapsing it into ``None`` would
    send the resolver to the fallback directory while a custom directory is
    configured (the fail-OPEN this contract forbids)."""
    pointer = tmp_path / REPORT_POINTER_FILENAME
    assert read_report_pointer(tmp_path) is None  # absent → None is correct

    original_read_text = Path.read_text

    def _denied(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == REPORT_POINTER_FILENAME:
            raise PermissionError(13, "pointer locked by another process")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    pointer.write_bytes(b"D:/Reports/custom\n")
    monkeypatch.setattr(Path, "read_text", _denied)
    with pytest.raises(ReportPointerUnavailable) as excinfo:
        read_report_pointer(tmp_path)
    assert excinfo.value.code == "REPORT_POINTER_UNAVAILABLE"
    # Binary garbage is equally "exists but unreadable".
    pointer.write_bytes(b"\xff\xfe\x00broken")
    with pytest.raises(ReportPointerUnavailable):
        read_report_pointer(tmp_path)
