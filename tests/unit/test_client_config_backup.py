"""P5-003: backup / restore state machine and the atomic write channel.

Restore must be reversible (fresh safety backup before touching anything),
sentinel backups must delete the target again, and every commit must verify
by reading the actual bytes back (AGENTS.md gotcha #7).
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evoblue_video_mcp.application.client_config.backup import (
    ABSENT_SENTINEL,
    BACKUP_SUFFIX,
    create_backup,
    list_backups,
    restore,
)
from evoblue_video_mcp.application.client_config.errors import (
    BackupNotFoundError,
    ConfigWriteError,
)
from evoblue_video_mcp.application.client_config.writes import (
    commit_atomic,
    remove_atomic,
    remove_file,
)

_TARGET = "config.toml"


def _clock(start: int):
    counter = {"n": 0}

    def _now() -> datetime:
        counter["n"] += 1
        return datetime(2026, 9, 7, 12, 0, counter["n"], tzinfo=UTC)

    return _now


def test_create_backup_on_existing_target(tmp_path: Path) -> None:
    target = tmp_path / _TARGET
    target.write_bytes(b"hello")
    info = create_backup(target, now=_clock(0))
    backup = tmp_path / info.name
    assert backup.is_file()
    assert info.name.startswith(_TARGET + BACKUP_SUFFIX)
    assert info.sha256 == backup.read_bytes().hex() or True  # hex vs sha: real check below
    import hashlib

    assert info.sha256 == hashlib.sha256(b"hello").hexdigest()
    assert info.was_absent is False
    assert backup.read_bytes() == b"hello"


def test_create_backup_on_absent_target_writes_sentinel(tmp_path: Path) -> None:
    target = tmp_path / _TARGET
    info = create_backup(target, now=_clock(0))
    backup = tmp_path / info.name
    assert info.was_absent is True
    assert backup.read_text(encoding="utf-8") == ABSENT_SENTINEL + "\n"


def test_same_second_backups_coexist_via_hex_suffix(tmp_path: Path) -> None:
    target = tmp_path / _TARGET
    target.write_bytes(b"one")
    fixed = lambda: datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)  # noqa: E731
    first = create_backup(target, now=fixed).name
    target.write_bytes(b"two")
    second = create_backup(target, now=fixed).name
    assert first != second
    assert (tmp_path / first).is_file() and (tmp_path / second).is_file()


def test_retention_keeps_newest_five(tmp_path: Path) -> None:
    target = tmp_path / _TARGET
    clock = _clock(0)
    names = []
    for i in range(7):
        target.write_bytes(f"v{i}".encode())
        names.append(create_backup(target, now=clock).name)
    kept = [path.name for path in sorted(tmp_path.glob("*" + BACKUP_SUFFIX + "*"))]
    assert len(kept) == 5
    assert names[-1] in kept  # newest kept
    assert names[0] not in kept  # oldest pruned


def test_restore_round_trip(tmp_path: Path) -> None:
    target = tmp_path / _TARGET
    target.write_bytes(b"original")
    backup = create_backup(target, now=_clock(0))
    target.write_bytes(b"edited after backup")
    result = restore(target, backup.name)
    assert result.restored_from == backup.name
    assert result.safety_backup != backup.name
    assert (tmp_path / result.safety_backup).read_bytes() == b"edited after backup"
    assert target.read_bytes() == b"original"


def test_restore_from_sentinel_deletes_target(tmp_path: Path) -> None:
    target = tmp_path / _TARGET
    pre_install = create_backup(target, now=_clock(0))  # target absent → sentinel
    target.write_bytes(b"installed")
    result = restore(target, pre_install.name)
    assert result.removed_target is True
    assert not target.exists()


def test_restore_oldest_backup_survives_retention_prune(tmp_path: Path) -> None:
    """P1 regression: with a full shelf, the safety backup prunes the oldest
    backup — which may be exactly the one the user chose to restore."""
    target = tmp_path / _TARGET
    clock = _clock(0)
    target.write_bytes(b"oldest-state")
    oldest = create_backup(target, now=clock)
    for i in range(4):  # fill the shelf to exactly RETENTION
        target.write_bytes(f"v{i}".encode())
        create_backup(target, now=clock)

    result = restore(target, oldest.name)
    assert result.restored_from == oldest.name
    assert target.read_bytes() == b"oldest-state"


def test_restore_unknown_or_foreign_name_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / _TARGET
    target.write_bytes(b"x")
    with pytest.raises(BackupNotFoundError):
        restore(target, "config.toml.evoblue-backup-20990101T000000Z-beef")
    other = tmp_path / "other.json"
    other.write_bytes(b"{}")
    foreign = create_backup(other, now=_clock(0))
    with pytest.raises(BackupNotFoundError):
        restore(target, foreign.name)


def test_list_backups_orders_newest_first_and_flags_current(tmp_path: Path) -> None:
    target = tmp_path / _TARGET
    clock = _clock(0)
    target.write_bytes(b"v1")
    first = create_backup(target, now=clock)
    target.write_bytes(b"v2")
    second = create_backup(target, now=clock)
    infos = list_backups(target)
    assert [info.name for info in infos] == [second.name, first.name]
    assert infos[0].matches_current is True
    assert infos[1].matches_current is False
    target.unlink()
    assert all(info.matches_current is None for info in list_backups(target))


def test_commit_atomic_verifies_by_reading_back(tmp_path: Path) -> None:
    target = tmp_path / _TARGET
    result = commit_atomic(target, b"payload")
    assert target.read_bytes() == b"payload"
    import hashlib

    assert result.sha256 == hashlib.sha256(b"payload").hexdigest()
    assert result.size_bytes == len(b"payload")
    assert list(tmp_path.glob("*.tmp-*")) == []  # no temp leftovers


def test_commit_atomic_refuses_to_create_directories(tmp_path: Path) -> None:
    target = tmp_path / "missing" / _TARGET
    with pytest.raises(ConfigWriteError, match="directory"):
        commit_atomic(target, b"x")
    assert not target.parent.exists()


def test_commit_atomic_cleans_temp_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    target = tmp_path / _TARGET
    target.write_bytes(b"old")
    target.chmod(0o444)
    # os.replace will fail on the read-only target on Windows; regardless of
    # platform behavior, the temp file must be cleaned up and the error raised.
    def _boom(src: object, dst: object) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        commit_atomic(target, b"new")
    target.chmod(0o644)
    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_remove_atomic_is_idempotent_and_verifies(tmp_path: Path) -> None:
    target = tmp_path / _TARGET
    remove_atomic(target)  # absent: fine
    target.write_bytes(b"x")
    remove_atomic(target)
    assert not target.exists()


def test_remove_file_absent_is_fine(tmp_path: Path) -> None:
    remove_file(tmp_path / "nope.bin")  # no raise
