"""Pre-migration database snapshot (INSTALLER_RELEASE_CONTRACT §6).

Before ``init_db`` applies a version jump, the engine snapshots the database
with the SQLite backup API (a raw file copy of a WAL database can capture a
stale/inconsistent image when ``-wal``/``-shm`` leftovers exist). The backup
is best-effort: failure logs ``MIGRATION_BACKUP_FAILED`` and the migration
proceeds, because Markdown reports are the true recovery asset
(MIGRATION_ROLLBACK rule 3) and the index rebuild can regenerate everything.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

BACKUP_NAME_TEMPLATE = "evoblue.db.bak-v{version}"
STABLE_ERROR_CODE = "MIGRATION_BACKUP_FAILED"


def backup_path_for(db_path: Path, current_version: int) -> Path:
    return db_path.parent / BACKUP_NAME_TEMPLATE.format(version=current_version)


def _read_schema_version(db_path: Path) -> int | None:
    """Current schema version via a read-only URI connection (never creates)."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return int(row[0]) if row is not None and row[0] is not None else 0


def _snapshot(db_path: Path, target: Path) -> None:
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(target))
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


async def backup_before_upgrade(
    engine: AsyncEngine, *, target_version: int
) -> Path | None:
    """Snapshot the database when ``init_db`` is about to upgrade it.

    Skips silently when the file is absent, has no version history (fresh
    install), or is already at/above the target. Returns the backup path, or
    None when no backup was made. Never raises: a failed backup must not
    block the migration (fail-open per contract §6).
    """
    raw = engine.url.database
    if not raw:
        return None
    db_path = Path(raw)
    if not db_path.is_file():
        return None
    try:
        current = await asyncio.to_thread(_read_schema_version, db_path)
    except Exception:
        logger.warning("%s: could not read schema version before upgrade", STABLE_ERROR_CODE)
        return None
    if current is None or current == 0 or current >= target_version:
        return None
    target = backup_path_for(db_path, current)
    try:
        await asyncio.to_thread(_snapshot, db_path, target)
    except Exception as exc:
        # Redacted log: exception type only, never paths or driver messages.
        logger.warning("%s: %s", STABLE_ERROR_CODE, type(exc).__name__)
        return None
    return target
