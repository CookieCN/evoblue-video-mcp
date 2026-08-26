"""Versioned schema migrations for the local SQLite store.

Each migration is an ordered (version, apply) pair. ``init_db`` records the
highest applied version in ``schema_migrations`` and only runs migrations newer
than that, so startup is idempotent. Migration DDL is frozen per version: v1
creates ``jobs`` with a unique ``idempotency_key``, and v3 rebuilds it with a
non-unique ``request_fingerprint`` so reuse-window re-analysis is possible.
"""

import time
from collections.abc import Awaitable, Callable
from typing import cast

from sqlalchemy import Table, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from evoblue_video_mcp.storage.models import AppSettings, JobArtifact

SCHEMA_VERSION = 3

Migration = Callable[[AsyncConnection], Awaitable[None]]

_JOBS_V1_DDL = """
CREATE TABLE jobs (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    job_id VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(64) NOT NULL,
    url TEXT NOT NULL,
    mode VARCHAR(32) NOT NULL,
    asr VARCHAR(32) NOT NULL,
    language VARCHAR(64),
    config_fingerprint VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    stage VARCHAR(32),
    progress INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    lease_owner VARCHAR(64),
    lease_expires_at FLOAT,
    cancel_requested_at FLOAT,
    next_retry_at FLOAT,
    error_code VARCHAR(64),
    error_detail TEXT,
    retryable BOOLEAN NOT NULL,
    created_at FLOAT NOT NULL,
    updated_at FLOAT NOT NULL,
    UNIQUE (idempotency_key)
)
"""

_JOBS_V3_REBUILD = [
    """
    CREATE TABLE jobs_new (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        job_id VARCHAR(64) NOT NULL,
        request_fingerprint VARCHAR(64) NOT NULL,
        url TEXT NOT NULL,
        mode VARCHAR(32) NOT NULL,
        asr VARCHAR(32) NOT NULL,
        language VARCHAR(64),
        config_fingerprint VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        stage VARCHAR(32),
        progress INTEGER NOT NULL,
        attempt INTEGER NOT NULL,
        max_attempts INTEGER NOT NULL,
        lease_owner VARCHAR(64),
        lease_expires_at FLOAT,
        cancel_requested_at FLOAT,
        next_retry_at FLOAT,
        error_code VARCHAR(64),
        error_detail TEXT,
        retryable BOOLEAN NOT NULL,
        created_at FLOAT NOT NULL,
        updated_at FLOAT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX ix_jobs_job_id ON jobs_new (job_id)",
    "CREATE INDEX ix_jobs_request_fingerprint ON jobs_new (request_fingerprint)",
    "CREATE INDEX ix_jobs_status ON jobs_new (status)",
    """
    INSERT INTO jobs_new (
        id, job_id, request_fingerprint, url, mode, asr, language,
        config_fingerprint, status, stage, progress, attempt, max_attempts,
        lease_owner, lease_expires_at, cancel_requested_at, next_retry_at,
        error_code, error_detail, retryable, created_at, updated_at
    )
    SELECT
        id, job_id, idempotency_key, url, mode, asr, language,
        config_fingerprint, status, stage, progress, attempt, max_attempts,
        lease_owner, lease_expires_at, cancel_requested_at, next_retry_at,
        error_code, error_detail, retryable, created_at, updated_at
    FROM jobs
    """,
    "DROP TABLE jobs",
    "ALTER TABLE jobs_new RENAME TO jobs",
]


async def _apply_v1(conn: AsyncConnection) -> None:
    await conn.execute(text(_JOBS_V1_DDL))


async def _apply_v2(conn: AsyncConnection) -> None:
    await conn.run_sync(cast(Table, AppSettings.__table__).create, checkfirst=True)


async def _apply_v3(conn: AsyncConnection) -> None:
    await conn.run_sync(cast(Table, JobArtifact.__table__).create, checkfirst=True)
    for statement in _JOBS_V3_REBUILD:
        await conn.execute(text(statement))


_MIGRATIONS: list[tuple[int, Migration]] = [
    (1, _apply_v1),
    (2, _apply_v2),
    (3, _apply_v3),
]


def _now() -> float:
    return time.time()


async def init_db(engine: AsyncEngine) -> None:
    """Bring the database up to ``SCHEMA_VERSION``, no-oping when already there."""
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, "
                "applied_at REAL NOT NULL)"
            )
        )
        current = (await conn.execute(text("SELECT MAX(version) FROM schema_migrations"))).scalar()
        applied = int(current) if current is not None else 0

        for version, migrate in _MIGRATIONS:
            if version <= applied:
                continue
            await migrate(conn)
            await conn.execute(
                text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :t)"),
                {"v": version, "t": _now()},
            )
