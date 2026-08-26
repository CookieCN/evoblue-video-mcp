"""A real v2 database upgrades to v3, preserving data and dropping idempotency_key."""

from sqlalchemy import text

from evoblue_video_mcp.storage import SCHEMA_VERSION, build_engine, init_db
from evoblue_video_mcp.storage.migrations import _apply_v1, _apply_v2


async def test_v2_database_upgrades_to_v3(tmp_path) -> None:
    db_path = tmp_path / "v2.db"
    engine = build_engine(db_path)

    # Build a real v2 database using the frozen migrations, then insert a row.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
            )
        )
        await _apply_v1(conn)
        await conn.execute(text("INSERT INTO schema_migrations VALUES (1, 1.0)"))
        await _apply_v2(conn)
        await conn.execute(text("INSERT INTO schema_migrations VALUES (2, 1.0)"))
        await conn.execute(
            text(
                "INSERT INTO jobs (job_id, idempotency_key, url, mode, asr,"
                " config_fingerprint, status, progress, attempt, max_attempts,"
                " retryable, created_at, updated_at)"
                " VALUES ('job-1', 'fp-1', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',"
                " 'auto', 'auto', 'cfg', 'queued', 0, 0, 3, 0, 1000.0, 1000.0)"
            )
        )

    # Upgrade to v3.
    await init_db(engine)

    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT MAX(version) FROM schema_migrations"))).scalar()
        assert version == SCHEMA_VERSION
        # The idempotency_key column is gone and its value moved to request_fingerprint.
        fingerprint = (
            await conn.execute(text("SELECT request_fingerprint FROM jobs WHERE job_id='job-1'"))
        ).scalar()
        assert fingerprint == "fp-1"
        idempotency_columns = (
            await conn.execute(text("PRAGMA table_info(jobs)"))
        ).all()
        assert all(col[1] != "idempotency_key" for col in idempotency_columns)

    await engine.dispose()
