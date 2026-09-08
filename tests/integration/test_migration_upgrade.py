"""Historical databases upgrade to the current schema, preserving data and indexes."""

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


_HISTORICAL_V5 = """
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
INSERT INTO schema_migrations VALUES (1, 1.0), (2, 1.0), (3, 1.0), (4, 1.0), (5, 1.0);
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
);
"""


async def test_frozen_v5_database_upgrades_to_current(tmp_path) -> None:
    db_path = tmp_path / "v5.db"
    sqlite3.connect(db_path).executescript(_HISTORICAL_V5)
    engine = build_engine(db_path)
    await init_db(engine)

    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT MAX(version) FROM schema_migrations"))).scalar()
        assert version == SCHEMA_VERSION
        old = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                    " AND name='model_install_state'"
                )
            )
        ).scalar()
        assert old is None
        tables = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                    " AND name IN ('model_install', 'active_model', 'model_download')"
                )
            )
        ).scalars().all()
        assert set(tables) == {"model_install", "active_model", "model_download"}
    await engine.dispose()


async def test_model_tables_created(tmp_path) -> None:
    engine = build_engine(tmp_path / "fresh.db")
    await init_db(engine)
    async with engine.connect() as conn:
        tables = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                    " AND name IN ('model_install', 'active_model', 'model_download')"
                )
            )
        ).scalars().all()
        assert set(tables) == {"model_install", "active_model", "model_download"}
    await engine.dispose()


_V8_TABLES = (
    "report_documents",
    "report_fts",
    "index_issues",
    "index_status",
)


async def test_fresh_database_reaches_v8(tmp_path) -> None:
    """P3-005 gate 1: an empty database migrates straight to v8."""
    engine = build_engine(tmp_path / "fresh.db")
    await init_db(engine)
    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT MAX(version) FROM schema_migrations"))).scalar()
        assert version == SCHEMA_VERSION == 8
        tables = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    f"AND name IN {_V8_TABLES}"
                )
            )
        ).scalars().all()
        assert set(tables) == set(_V8_TABLES)
        state = (await conn.execute(text("SELECT state FROM index_status WHERE id = 1"))).scalar()
        assert state == "idle"
    await engine.dispose()


async def test_v7_database_upgrades_to_v8_preserving_data(tmp_path) -> None:
    """P3-005 gate 2: a real v7 database (chain stopped at 7) upgrades to v8."""
    db_path = tmp_path / "v7.db"
    engine7 = build_engine(db_path)
    await init_db(engine7, target_version=7)
    await engine7.dispose()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (job_id, request_fingerprint, url, mode, asr, "
        "config_fingerprint, status, progress, attempt, max_attempts, "
        "retryable, created_at, updated_at) VALUES "
        "('job-legacy', 'fp-legacy', 'https://www.youtube.com/watch?v=x', "
        "'auto', 'auto', 'cfg', 'completed', 100, 1, 3, 0, 1000.0, 2000.0)"
    )
    conn.execute(
        "INSERT INTO app_settings (id, setup_completed, updated_at) VALUES (1, 1, 1000.0)"
    )
    conn.commit()
    conn.close()

    engine = build_engine(db_path)
    await init_db(engine)
    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT MAX(version) FROM schema_migrations"))).scalar()
        assert version == SCHEMA_VERSION == 8
        legacy = (
            await conn.execute(
                text("SELECT status FROM jobs WHERE job_id = 'job-legacy'")
            )
        ).scalar()
        assert legacy == "completed"
        setup = (await conn.execute(text("SELECT setup_completed FROM app_settings"))).scalar()
        assert setup == 1
        tables = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    f"AND name IN {_V8_TABLES}"
                )
            )
        ).scalars().all()
        assert set(tables) == set(_V8_TABLES)
    await engine.dispose()


async def test_upgrade_to_v8_matches_fresh_install(tmp_path) -> None:
    """P3-005 gate 2/3: upgraded v7 and fresh installs converge on one schema."""
    v7_path = tmp_path / "v7.db"
    engine7 = build_engine(v7_path)
    await init_db(engine7, target_version=7)
    await engine7.dispose()

    upgraded = build_engine(v7_path)
    await init_db(upgraded)
    fresh = build_engine(tmp_path / "fresh.db")
    await init_db(fresh)

    assert await _schema_snapshot(fresh) == await _schema_snapshot(upgraded)

    await upgraded.dispose()
    await fresh.dispose()


async def test_init_db_v8_is_idempotent(tmp_path) -> None:
    """P3-005 gate 3: rerunning init_db on a v8 database changes nothing."""
    engine = build_engine(tmp_path / "db.sqlite")
    await init_db(engine)
    before = await _schema_snapshot(engine)
    await init_db(engine)
    after = await _schema_snapshot(engine)
    assert before == after
    await engine.dispose()


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


async def test_upgrade_creates_versioned_backup(tmp_path) -> None:
    """P7-005 (contract §6): a version jump snapshots the db as evoblue.db.bak-v7."""
    db_path = tmp_path / "evoblue.db"
    engine7 = build_engine(db_path)
    await init_db(engine7, target_version=7)
    await engine7.dispose()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (job_id, request_fingerprint, url, mode, asr, "
        "config_fingerprint, status, progress, attempt, max_attempts, "
        "retryable, created_at, updated_at) VALUES "
        "('job-backup', 'fp-backup', 'https://www.youtube.com/watch?v=x', "
        "'auto', 'auto', 'cfg', 'completed', 100, 1, 3, 0, 1000.0, 2000.0)"
    )
    conn.commit()
    conn.close()

    engine = build_engine(db_path)
    await init_db(engine)
    await engine.dispose()

    backup = tmp_path / "evoblue.db.bak-v7"
    assert backup.is_file(), "upgrade must snapshot the pre-migration database"
    snap = sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)
    try:
        version = snap.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        assert version == 7
        legacy = snap.execute(
            "SELECT status FROM jobs WHERE job_id = 'job-backup'"
        ).fetchone()[0]
        assert legacy == "completed"
    finally:
        snap.close()


async def test_fresh_init_creates_no_backup(tmp_path) -> None:
    engine = build_engine(tmp_path / "evoblue.db")
    await init_db(engine)
    await engine.dispose()
    assert not (tmp_path / "evoblue.db.bak-v0").exists()
    assert not list(tmp_path.glob("*.bak-*"))


async def test_no_backup_when_already_current(tmp_path) -> None:
    db_path = tmp_path / "evoblue.db"
    engine = build_engine(db_path)
    await init_db(engine)
    await engine.dispose()
    engine2 = build_engine(db_path)
    await init_db(engine2)  # no-op at current version
    await engine2.dispose()
    assert not list(tmp_path.glob("*.bak-*"))


async def test_backup_failure_does_not_block_migration(
    tmp_path, monkeypatch, caplog
) -> None:
    """Fail-open per contract §6: MIGRATION_BACKUP_FAILED, migration proceeds."""
    import logging

    db_path = tmp_path / "evoblue.db"
    engine7 = build_engine(db_path)
    await init_db(engine7, target_version=7)
    await engine7.dispose()

    from evoblue_video_mcp.storage import backup as backup_mod

    def broken_snapshot(src, dst):
        raise sqlite3.OperationalError("injected failure")

    monkeypatch.setattr(backup_mod, "_snapshot", broken_snapshot)
    with caplog.at_level(logging.WARNING):
        engine = build_engine(db_path)
        await init_db(engine)
    async with engine.connect() as conn:
        version = (
            await conn.execute(text("SELECT MAX(version) FROM schema_migrations"))
        ).scalar()
        assert version == SCHEMA_VERSION
    await engine.dispose()
    assert any("MIGRATION_BACKUP_FAILED" in r.message for r in caplog.records)
