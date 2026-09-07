"""Backup / restore state machine (contract §5).

Backups live next to their target (same directory → same-filesystem
``os.replace``), named ``<filename><BACKUP_SUFFIX><UTC ts>Z-<4 hex>``, newest
``BACKUP_RETENTION`` kept. A target that did not exist is backed up as a
one-line sentinel file so "restore the state before install" can delete it
again — restore is always reversible: it takes a fresh safety backup of the
current state before touching anything, and returns both backup names.

Format-agnostic: parsing the restored bytes back to life is the adapter's
job (it knows the container format); this module guarantees bytes.
"""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evoblue_video_mcp.application.client_config.errors import BackupNotFoundError
from evoblue_video_mcp.application.client_config.models import BackupInfo
from evoblue_video_mcp.application.client_config.writes import (
    commit_atomic,
    remove_atomic,
    remove_file,
    sha256_hex,
)

BACKUP_SUFFIX: str = ".evoblue-backup-"
BACKUP_RETENTION: int = 5
ABSENT_SENTINEL: str = "EVOBLUE_ABSENT_SENTINEL"

_TIMESTAMP_RE = re.compile(
    re.escape(BACKUP_SUFFIX) + r"(\d{8}T\d{6})Z-[0-9a-f]{4}$"
)


@dataclass(frozen=True)
class RestoreResult:
    restored_from: str
    safety_backup: str
    removed_target: bool


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sentinel_payload() -> bytes:
    return (ABSENT_SENTINEL + "\n").encode("utf-8")


def _is_sentinel(data: bytes) -> bool:
    return data == _sentinel_payload()


def create_backup(
    target: Path, *, now: Callable[[], datetime] = _utcnow
) -> BackupInfo:
    """Capture the current target bytes (sentinel when absent) as a sibling."""
    try:
        current = target.read_bytes()
        was_absent = False
    except FileNotFoundError:
        current = _sentinel_payload()
        was_absent = True
    moment = now()
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    name = (
        f"{target.name}{BACKUP_SUFFIX}{stamp}-{os.urandom(2).hex()}"
    )
    backup_path = target.with_name(name)
    commit_atomic(backup_path, current)
    _prune(target)
    return BackupInfo(
        name=name,
        created_at=moment.timestamp(),
        size_bytes=len(current),
        sha256=sha256_hex(current),
        was_absent=was_absent,
    )


def list_backups(target: Path) -> list[BackupInfo]:
    """All backups of ``target``, newest first, with matches_current."""
    try:
        current_sha: str | None = sha256_hex(target.read_bytes())
    except FileNotFoundError:
        current_sha = None
    infos: list[BackupInfo] = []
    for path in _backup_paths(target):
        data = path.read_bytes()
        infos.append(
            BackupInfo(
                name=path.name,
                created_at=_created_at(path),
                size_bytes=len(data),
                sha256=sha256_hex(data),
                was_absent=_is_sentinel(data),
                matches_current=None if current_sha is None else sha256_hex(data) == current_sha,
            )
        )
    return infos


def restore(target: Path, backup_name: str) -> RestoreResult:
    """Restore ``backup_name`` over ``target`` — reversibly (contract §5).

    Reads the selected backup into memory BEFORE taking the safety backup:
    the safety backup triggers retention pruning, and with a full shelf that
    prune deletes the oldest backup — which may be exactly the one the user
    chose to restore. A sentinel backup restores by deleting the target.
    """
    backup_path = _resolve_backup(target, backup_name)
    data = backup_path.read_bytes()
    safety = create_backup(target)
    if _is_sentinel(data):
        remove_atomic(target)
        return RestoreResult(
            restored_from=backup_path.name,
            safety_backup=safety.name,
            removed_target=True,
        )
    commit_atomic(target, data)
    return RestoreResult(
        restored_from=backup_path.name,
        safety_backup=safety.name,
        removed_target=False,
    )


def _backup_paths(target: Path) -> list[Path]:
    pattern = f"{target.name}{BACKUP_SUFFIX}*"
    found = [
        path
        for path in target.parent.glob(pattern)
        if path.is_file() and _TIMESTAMP_RE.search(path.name)
    ]
    return sorted(found, key=lambda path: path.name, reverse=True)


def _resolve_backup(target: Path, backup_name: str) -> Path:
    candidate = target.with_name(backup_name)
    if (
        backup_name == target.name
        or not _TIMESTAMP_RE.search(backup_name)
        or not backup_name.startswith(target.name + BACKUP_SUFFIX)
        or not candidate.is_file()
    ):
        raise BackupNotFoundError(f"no such backup for this target: {backup_name}")
    return candidate


def _created_at(path: Path) -> float:
    match = _TIMESTAMP_RE.search(path.name)
    if match is None:  # pragma: no cover - _backup_paths already filtered
        return 0.0
    moment = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    return moment.timestamp()


def _prune(target: Path) -> None:
    """Keep only the newest BACKUP_RETENTION backups (best effort)."""
    for path in _backup_paths(target)[BACKUP_RETENTION:]:
        remove_file(path)
