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

SCHEMA_VERSION = 8

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


_MODEL_INSTALL_STATE_V5_DDL = """
CREATE TABLE model_install_state (
    model_id VARCHAR(64) NOT NULL PRIMARY KEY,
    version VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    downloaded_bytes INTEGER NOT NULL,
    expected_size_bytes INTEGER NOT NULL,
    temp_path VARCHAR,
    installed_path VARCHAR,
    content_sha256 VARCHAR(64),
    error_code VARCHAR(64),
    error_detail TEXT,
    updated_at FLOAT NOT NULL
)
"""

_MODEL_INSTALL_V6_DDL = """
CREATE TABLE model_install (
    model_id VARCHAR(64) NOT NULL,
    version VARCHAR(64) NOT NULL,
    installed_path VARCHAR NOT NULL,
    installed_at FLOAT NOT NULL,
    PRIMARY KEY (model_id, version)
)
"""

_ACTIVE_MODEL_V6_DDL = """
CREATE TABLE active_model (
    model_id VARCHAR(64) NOT NULL PRIMARY KEY,
    active_version VARCHAR(64) NOT NULL,
    updated_at FLOAT NOT NULL
)
"""

_MODEL_DOWNLOAD_V6_DDL = """
CREATE TABLE model_download (
    operation_id VARCHAR(64) NOT NULL PRIMARY KEY,
    model_id VARCHAR(64) NOT NULL,
    version VARCHAR(64) NOT NULL,
    source_url VARCHAR NOT NULL,
    source_kind VARCHAR(16) NOT NULL,
    expected_sha256 VARCHAR(64) NOT NULL,
    expected_size_bytes INTEGER NOT NULL,
    downloaded_bytes INTEGER NOT NULL,
    temp_path VARCHAR,
    etag VARCHAR,
    last_modified VARCHAR,
    status VARCHAR(32) NOT NULL,
    error_code VARCHAR(64),
    error_detail TEXT,
    revision INTEGER NOT NULL,
    created_at FLOAT NOT NULL,
    updated_at FLOAT NOT NULL
)
"""

_MODEL_DOWNLOAD_ACTIVE_INDEX_V6 = """
CREATE UNIQUE INDEX uq_model_download_active ON model_download (model_id)
WHERE status IN ('pending', 'downloading', 'verifying', 'installing')
"""


async def _apply_v1(conn: AsyncConnection) -> None:
    await conn.execute(text(_JOBS_V1_DDL))


async def _apply_v2(conn: AsyncConnection) -> None:
    await conn.execute(text(_APP_SETTINGS_V2_DDL))


async def _apply_v3(conn: AsyncConnection) -> None:
    await conn.execute(text(_JOB_ARTIFACTS_V3_DDL))
    await conn.execute(text(_JOB_ARTIFACTS_V3_INDEX))
    for statement in _JOBS_V3_REBUILD:
        await conn.execute(text(statement))


async def _apply_v4(conn: AsyncConnection) -> None:
    await conn.execute(text("ALTER TABLE app_settings ADD COLUMN llm_base_url VARCHAR"))
    await conn.execute(text("ALTER TABLE app_settings ADD COLUMN llm_credential_ref VARCHAR(128)"))


async def _apply_v5(conn: AsyncConnection) -> None:
    await conn.execute(text(_MODEL_INSTALL_STATE_V5_DDL))


async def _apply_v6(conn: AsyncConnection) -> None:
    await conn.execute(text("DROP TABLE model_install_state"))
    await conn.execute(text(_MODEL_INSTALL_V6_DDL))
    await conn.execute(text(_ACTIVE_MODEL_V6_DDL))
    await conn.execute(text(_MODEL_DOWNLOAD_V6_DDL))
    await conn.execute(text(_MODEL_DOWNLOAD_ACTIVE_INDEX_V6))


async def _apply_v7(conn: AsyncConnection) -> None:
    tables = set(
        (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")))
        .scalars()
        .all()
    )
    if "jobs" in tables:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN asr_provider_id VARCHAR(64)"))
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN asr_model_id VARCHAR(64)"))
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN asr_model_version VARCHAR(64)"))
        await conn.execute(
            text("ALTER TABLE jobs ADD COLUMN asr_recommendation_model_id VARCHAR(64)")
        )
    if "app_settings" in tables:
        await conn.execute(text("ALTER TABLE app_settings ADD COLUMN asr_provider VARCHAR(64)"))
        await conn.execute(
            text("ALTER TABLE app_settings ADD COLUMN whisper_cpp_executable VARCHAR")
        )


# P3 contract: docs/FTS5_SCHEMA.md §2 — these statements are transcribed
# character-for-character from the frozen contract DDL blocks. A contract test
# (tests/contract/test_p3_v8_ddl_drift.py) fails if the two ever diverge, so
# edit the contract document first, then copy its blocks back here.
_V8_STATEMENTS: list[str] = [
    """
CREATE TABLE report_documents (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    job_id VARCHAR(64) NOT NULL,
    analysis_id VARCHAR(64) NOT NULL,
    title TEXT NOT NULL,
    platform VARCHAR(64) NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    video_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    published_at REAL,
    analyzed_at REAL NOT NULL,
    language VARCHAR(64) NOT NULL DEFAULT '',
    summary_mode VARCHAR(32) NOT NULL DEFAULT '',
    asr_provider VARCHAR(64) NOT NULL DEFAULT '',
    asr_model VARCHAR(64) NOT NULL DEFAULT '',
    asr_model_version VARCHAR(64) NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    summary_preview TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    byte_size INTEGER,
    doc_source VARCHAR(16) NOT NULL DEFAULT 'pipeline',
    doc_status VARCHAR(16) NOT NULL DEFAULT 'active',
    indexed_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
""",
    """
CREATE UNIQUE INDEX ix_report_documents_job_id ON report_documents (job_id)
""",
    """
CREATE UNIQUE INDEX ix_report_documents_analysis_id ON report_documents (analysis_id)
""",
    """
CREATE INDEX ix_report_documents_analyzed_at ON report_documents (analyzed_at)
""",
    """
CREATE VIRTUAL TABLE report_fts USING fts5(
    title,
    tags,
    author,
    summary,
    transcript,
    url,
    tokenize='unicode61 remove_diacritics 2'
)
""",
    """
CREATE TABLE index_issues (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    issue_code VARCHAR(64) NOT NULL,
    relative_path TEXT NOT NULL,
    detail TEXT,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    resolved_at REAL
)
""",
    """
CREATE UNIQUE INDEX uq_index_issues_open ON index_issues (issue_code, relative_path)
WHERE resolved_at IS NULL
""",
    """
CREATE INDEX ix_index_issues_open ON index_issues (resolved_at)
""",
    """
CREATE TABLE index_status (
    id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
    state VARCHAR(16) NOT NULL DEFAULT 'idle',
    started_at REAL,
    finished_at REAL,
    scanned INTEGER NOT NULL DEFAULT 0,
    indexed INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    quarantined INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    removed INTEGER NOT NULL DEFAULT 0,
    purged INTEGER NOT NULL DEFAULT 0,
    last_error_code VARCHAR(64)
)
""",
    """
INSERT INTO index_status (id, state) VALUES (1, 'idle')
""",
]


async def _apply_v8(conn: AsyncConnection) -> None:
    for statement in _V8_STATEMENTS:
        await conn.execute(text(statement))


_MIGRATIONS: list[tuple[int, Migration]] = [
    (1, _apply_v1),
    (2, _apply_v2),
    (3, _apply_v3),
    (4, _apply_v4),
    (5, _apply_v5),
    (6, _apply_v6),
    (7, _apply_v7),
    (8, _apply_v8),
]


def _now() -> float:
    return time.time()


async def init_db(engine: AsyncEngine, *, target_version: int | None = None) -> None:
    """Bring the database up to ``SCHEMA_VERSION``, no-oping when already there.

    ``target_version`` caps how far the migration chain runs; it exists so tests
    can materialize a genuine historical schema (e.g. v7) before upgrading.
    Production callers must leave it unset. WAL mode is set per connection in
    ``build_engine`` (it cannot switch from inside a transaction).
    """
    # P7 (INSTALLER_RELEASE_CONTRACT §6): snapshot before any version jump.
    if target_version is None:
        from evoblue_video_mcp.storage.backup import backup_before_upgrade

        await backup_before_upgrade(engine, target_version=SCHEMA_VERSION)
    async with engine.begin() as conn:
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
            if target_version is not None and version > target_version:
                continue
            await migrate(conn)
            await conn.execute(
                text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :t)"),
                {"v": version, "t": _now()},
            )
