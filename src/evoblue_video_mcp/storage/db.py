"""Async engine construction for the local SQLite store."""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def build_engine(db_path: str | Path) -> AsyncEngine:
    """Create a file-backed async engine; ``timeout`` maps to SQLite busy timeout."""
    path = Path(db_path).resolve()
    return create_async_engine(
        f"sqlite+aiosqlite:///{path.as_posix()}",
        connect_args={"timeout": 5.0},
    )
