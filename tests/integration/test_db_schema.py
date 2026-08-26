"""Schema initialization and migration versioning are idempotent."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from evoblue_video_mcp.storage import SCHEMA_VERSION, init_db


async def test_init_db_creates_schema_and_records_version(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT MAX(version) FROM schema_migrations"))).scalar()
        assert version == SCHEMA_VERSION


async def test_init_db_is_idempotent(engine: AsyncEngine) -> None:
    # Re-running against an already-initialized engine must not error or bump versions.
    await init_db(engine)
    await init_db(engine)
    async with engine.connect() as conn:
        versions = (
            await conn.execute(text("SELECT version FROM schema_migrations"))
        ).scalars().all()
        assert versions == list(range(1, SCHEMA_VERSION + 1))


async def test_jobs_table_exists(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
            )
        ).scalar()
        assert row == "jobs"


async def test_job_artifacts_table_exists(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='job_artifacts'"
                )
            )
        ).scalar()
        assert row == "job_artifacts"


async def test_app_settings_has_runtime_llm_columns(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        columns = {
            row[1] for row in (await conn.execute(text("PRAGMA table_info(app_settings)"))).all()
        }
    assert {"llm_base_url", "llm_credential_ref"} <= columns
