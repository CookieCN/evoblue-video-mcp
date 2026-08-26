"""A frozen historical v2 database upgrades to v3, preserving data and index names."""

import sqlite3

from sqlalchemy import text

from evoblue_video_mcp.storage import SCHEMA_VERSION, build_engine, init_db

# Frozen DDL that reproduces what an older SQLAlchemy build created: the jobs
# table carried idempotency_key plus unique/normal indexes whose names are
# global in SQLite, so v3 must drop the old table before creating new indexes.
_HISTORICAL_V2 = """
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
INSERT INTO schema_migrations VALUES (1, 1.0), (2, 1.0);

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
);
CREATE UNIQUE INDEX ix_jobs_job_id ON jobs (job_id);
CREATE UNIQUE INDEX ix_jobs_idempotency_key ON jobs (idempotency_key);
CREATE INDEX ix_jobs_status ON jobs (status);

CREATE TABLE app_settings (
    id INTEGER NOT NULL PRIMARY KEY,
    setup_completed BOOLEAN NOT NULL,
    report_directory VARCHAR,
    llm_provider VARCHAR(32),
    llm_model VARCHAR(128),
    updated_at FLOAT NOT NULL
);

INSERT INTO jobs (
    job_id, idempotency_key, url, mode, asr, config_fingerprint, status,
    progress, attempt, max_attempts, retryable, created_at, updated_at
) VALUES (
    'job-1', 'fp-1', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'auto', 'auto',
    'cfg', 'queued', 0, 0, 3, 0, 1000.0, 1000.0
);
"""


async def test_frozen_v2_database_upgrades_to_v3(tmp_path) -> None:
    db_path = tmp_path / "v2.db"
    sqlite3.connect(db_path).executescript(_HISTORICAL_V2)

    engine = build_engine(db_path)
    await init_db(engine)

    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT MAX(version) FROM schema_migrations"))).scalar()
        assert version == SCHEMA_VERSION
        fingerprint = (
            await conn.execute(text("SELECT request_fingerprint FROM jobs WHERE job_id='job-1'"))
        ).scalar()
        assert fingerprint == "fp-1"
        columns = (await conn.execute(text("PRAGMA table_info(jobs)"))).all()
        assert all(col[1] != "idempotency_key" for col in columns)
        artifacts = (
            await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='job_artifacts'")
            )
        ).scalar()
        assert artifacts == "job_artifacts"

    await engine.dispose()


async def _schema_snapshot(engine) -> dict:
    async with engine.connect() as conn:
        tables = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                    " AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
        ).scalars().all()
        snapshot: dict = {}
        for table in tables:
            columns = (await conn.execute(text(f"PRAGMA table_info({table})"))).all()
            indexes = (await conn.execute(text(f"PRAGMA index_list({table})"))).all()
            snapshot[table] = (columns, indexes)
        return snapshot


async def test_fresh_install_matches_upgraded_v2(tmp_path) -> None:
    fresh_engine = build_engine(tmp_path / "fresh.db")
    await init_db(fresh_engine)

    v2_db = tmp_path / "v2.db"
    sqlite3.connect(v2_db).executescript(_HISTORICAL_V2)
    v2_engine = build_engine(v2_db)
    await init_db(v2_engine)

    assert await _schema_snapshot(fresh_engine) == await _schema_snapshot(v2_engine)

    await fresh_engine.dispose()
    await v2_engine.dispose()
