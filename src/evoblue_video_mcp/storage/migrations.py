"""Versioned schema migrations for the local SQLite store.

Each migration is an ordered (version, apply) pair. ``init_db`` records the
highest applied version in ``schema_migrations`` and only runs migrations newer
than that, so startup is idempotent. Every migration uses frozen SQL defined in
this file — never the current ORM models — so a fresh install and an upgrade from
any historical version converge on the same schema.
"""

import time
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

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

_APP_SETTINGS_V2_DDL = """
CREATE TABLE app_settings (
    id INTEGER NOT NULL PRIMARY KEY,
    setup_completed BOOLEAN NOT NULL,
    report_directory VARCHAR,
    llm_provider VARCHAR(32),
    llm_model VARCHAR(128),
    updated_at FLOAT NOT NULL
)
"""

_JOB_ARTIFACTS_V3_DDL = """
CREATE TABLE job_artifacts (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    job_id VARCHAR(64) NOT NULL,
    stage VARCHAR(32) NOT NULL,
    artifact_type VARCHAR(64) NOT NULL,
    input_fingerprint VARCHAR(64) NOT NULL,
    schema_version INTEGER NOT NULL,
    storage_kind VARCHAR(16) NOT NULL,
    payload_json TEXT,
    relative_path VARCHAR,
    content_hash VARCHAR(64),
    byte_size INTEGER,
    created_at FLOAT NOT NULL,
    updated_at FLOAT NOT NULL,
    UNIQUE (job_id, artifact_type, input_fingerprint)
)
"""

_JOB_ARTIFACTS_V3_INDEX = (
    "CREATE INDEX ix_job_artifacts_job_id ON job_artifacts (job_id)"
)

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
    "CREATE UNIQUE INDEX ix_jobs_job_id ON jobs (job_id)",
    "CREATE INDEX ix_jobs_request_fingerprint ON jobs (request_fingerprint)",
    "CREATE INDEX ix_jobs_status ON jobs (status)",
]


async def _apply_v1(conn: AsyncConnection) -> None:
    await conn.execute(text(_JOBS_V1_DDL))


async def _apply_v2(conn: AsyncConnection) -> None:
    await conn.execute(text(_APP_SETTINGS_V2_DDL))


async def _apply_v3(conn: AsyncConnection) -> None:
    await conn.execute(text(_JOB_ARTIFACTS_V3_DDL))
    await conn.execute(text(_JOB_ARTIFACTS_V3_INDEX))
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
