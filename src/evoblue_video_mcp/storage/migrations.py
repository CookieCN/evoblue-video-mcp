"""Versioned schema migrations for the local SQLite store.

Each migration is an ordered (version, apply) pair. ``init_db`` records the
highest applied version in ``schema_migrations`` and only runs migrations newer
than that, so startup is idempotent and future schema changes slot in behind a
new version instead of mutating an existing one in place.
"""

import time
from collections.abc import Awaitable, Callable
from typing import cast

from sqlalchemy import Table, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from evoblue_video_mcp.storage.models import AppSettings, Job

SCHEMA_VERSION = 2

Migration = Callable[[AsyncConnection], Awaitable[None]]


async def _apply_v1(conn: AsyncConnection) -> None:
    await conn.run_sync(cast(Table, Job.__table__).create, checkfirst=True)


async def _apply_v2(conn: AsyncConnection) -> None:
    await conn.run_sync(cast(Table, AppSettings.__table__).create, checkfirst=True)


_MIGRATIONS: list[tuple[int, Migration]] = [
    (1, _apply_v1),
    (2, _apply_v2),
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
