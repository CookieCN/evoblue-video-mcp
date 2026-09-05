"""Async engine construction for the local SQLite store."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Connection, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

#: Execution option selecting ``BEGIN IMMEDIATE`` for one transaction. Set via
#: ``session.connection(execution_options=IMMEDIATE_WRITE_OPTS)`` — the option
#: must be present BEFORE the transaction starts, because the begin hook below
#: reads it inside ``_begin_impl``. (``Mapping[str, Any]``-typed aliases: the
#: ``execution_options`` parameter is a TypedDict, which rejects non-literal
#: keys.)
BEGIN_IMMEDIATE_OPTION: Final[str] = "evoblue_begin_immediate"
IMMEDIATE_WRITE_OPTS: Final[Mapping[str, Any]] = {BEGIN_IMMEDIATE_OPTION: True}
DEFERRED_WRITE_OPTS: Final[Mapping[str, Any]] = {BEGIN_IMMEDIATE_OPTION: False}


_TRANSACTION_HAS_DML: Final[str] = "evoblue_tx_has_dml"
#: Authorizer actions that constitute a WRITE: everything that can change the
#: DATABASE or the CONNECTION'S PERSISTENT STATE — row data (INSERT/UPDATE/
#: DELETE, including writes fired from triggers), schema (CREATE/DROP/ALTER of
#: tables, indexes, views, triggers, virtual tables), PRAGMA (``user_version``
#: and friends persist writes; READ pragmas report the same action code and are
#: flagged too — fail-closed: refusing a read-only transaction is cheap,
#: silently publishing an unowned write is not), and REINDEX / ANALYZE /
#: ATTACH / DETACH (index rewrites, sqlite_stat1 updates, attached databases).
#: Deliberately NOT flagged — pure reads or transaction control: READ, SELECT,
#: RECURSIVE, FUNCTION (bm25/snippet are part of the search read path),
#: SAVEPOINT, TRANSACTION (savepoints carry the report_repository pairing
#: writes, which their owning transaction already covers).
#: Reported at PREPARE time regardless of how the statement is spelled
#: (comments, ``WITH...UPDATE``). Migrations run DDL/PRAGMA on engine-level
#: transactions that never reach the guard, so the broad set only bites
#: sessions that actually hold writes.
_WRITE_ACTIONS: Final[frozenset[int]] = frozenset(
    {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_VTABLE,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_VTABLE,
        sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_REINDEX,
        sqlite3.SQLITE_ANALYZE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
    }
)


async def ensure_immediate_transaction(session: AsyncSession) -> None:
    """Put ``session`` at the start of a ``BEGIN IMMEDIATE`` transaction
    without ever publishing caller-owned work (§6).

    * no open transaction → start the IMMEDIATE transaction;
    * already inside an IMMEDIATE transaction → return unchanged (idempotent;
      a single owner, a single commit);
    * inside a DEFERRED transaction that has executed DML → REFUSE:
      ``session.new/dirty/deleted`` cannot see Core DML sent via
      ``execute()``, so the transaction's write state is tracked at the
      engine event layer (``_TRANSACTION_HAS_DML``) — committing would
      publish caller-owned writes a later rollback cannot undo;
    * inside a DEFERRED transaction with pending ORM changes → REFUSE as
      well (an empty commit would flush and publish them);
    * inside a READ-ONLY deferred transaction → close it with an EMPTY
      commit (nothing to publish; a rollback would instead expire the
      caller's already-loaded instances) and start the write IMMEDIATE.
    """
    if session.in_transaction():
        conn = await session.connection()
        sync_conn = conn.sync_connection
        assert sync_conn is not None  # an open transaction implies a connection
        if sync_conn.get_execution_options().get(BEGIN_IMMEDIATE_OPTION):
            return
        if (
            session.new
            or session.dirty
            or session.deleted
            or sync_conn.info.get(_TRANSACTION_HAS_DML, False)
        ):
            raise RuntimeError(
                "_begin_immediate refused: the open transaction already holds "
                "caller-owned writes (ORM changes or executed DML) — commit "
                "or roll it back before starting a write"
            )
        # Read-only transaction: close it without expiring anything.
        await session.commit()
    await session.connection(execution_options=IMMEDIATE_WRITE_OPTS)


@asynccontextmanager
async def immediate_write_transaction(
    session: AsyncSession,
) -> AsyncIterator[None]:
    """Run a write as ``BEGIN IMMEDIATE`` on a session that first reads.

    A deferred transaction that reads first and writes later fails with an
    unretryable ``BUSY_SNAPSHOT`` when another writer commits in between
    (WAL): the busy timeout never fires for a snapshot conflict, so the
    statement dies instantly with "database is locked". Starting the write
    as IMMEDIATE takes the write lock up front — contention then degrades to
    the busy timeout instead. The lock is held only for the short write:
    long read phases (pipeline handlers, rescan batches) stay deferred, so
    the heartbeat session and concurrent readers are never starved.

    Transaction ownership is explicit: entry goes through
    ``ensure_immediate_transaction`` (never publishing caller-owned work);
    the body's work is committed ONLY on success (``else``), rolled back on
    ANY exception including cancellation (``except BaseException``) — a
    half-finished write is never committed by teardown, and a failed commit
    propagates instead of being swallowed. The commit itself is
    cancellation-safe: a cancel can land on the commit's await AFTER SQLite
    already made it durable, so the outcome is awaited to a verdict through
    a shielded task — durable success propagates the cancellation with the
    write KEPT (compensating then would manufacture a split state), durable
    failure re-raises the commit error so the caller compensates against a
    database that truly did not adopt the value. The ``finally`` block only
    restores the deferred execution option and never raises over the
    original outcome.
    """
    await ensure_immediate_transaction(session)
    try:
        yield
    except BaseException:
        with suppress(Exception):
            await session.rollback()
        raise
    else:
        commit_task = asyncio.ensure_future(session.commit())
        try:
            await asyncio.shield(commit_task)
        except asyncio.CancelledError:
            # The commit MAY already be durable — get its verdict before
            # propagating. The verdict wait itself is REPEATED-CANCELLATION
            # safe: every external cancel lands on the shield (never on the
            # task), and we keep waiting in a loop until the commit task has
            # actually finished. We never cancel the task ourselves.
            while not commit_task.done():
                try:
                    await asyncio.shield(commit_task)
                except asyncio.CancelledError:
                    continue  # another external cancel — verdict still pending
            exc = commit_task.exception()
            if exc is not None:
                raise exc from None  # durable FAILURE → caller compensates
            raise  # durable SUCCESS → keep the write, propagate the cancel
    finally:
        with suppress(Exception):
            await session.connection(execution_options=DEFERRED_WRITE_OPTS)


def build_engine(db_path: str | Path) -> AsyncEngine:
    """Create a file-backed async engine; ``timeout`` maps to SQLite busy timeout.

    The isolation setup is SQLAlchemy's documented SQLite savepoint recipe
    (docs: "Serializable isolation / Savepoints / Transactional DDL"): the
    sqlite3 driver's legacy implicit-transaction mode turns ``RELEASE
    SAVEPOINT`` on the outermost savepoint into a COMMIT, which would silently
    break ``begin_nested()`` — the guarantee ``storage/report_repository.py``
    uses for pairing-atomic ``report_documents``/``report_fts`` writes. We
    disable the driver's transaction management (``isolation_level = None`` in
    the connect hook) and emit an explicit ``BEGIN`` per logical transaction in
    the begin hook, so SAVEPOINT semantics are native. Driver-level
    ``AUTOCOMMIT`` (``isolation_level="AUTOCOMMIT"``) must NOT be combined with
    the manual begin hook — the two modes are mutually exclusive per the docs.
    """
    path = Path(db_path).resolve()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path.as_posix()}",
        # cached_statements=0 disables the sqlite3 module's per-connection
        # prepared-statement cache: a cached statement is re-executed WITHOUT
        # prepare, so the authorizer (write tracking, see the connect hook)
        # would never fire for the second execution of the same SQL string —
        # on pooled connections that is the COMMON case (parameterized
        # Core/ORM DML shares one SQL string), silently blinding the guard.
        # Every statement re-prepares: the authorizer observes every write.
        connect_args={"timeout": 5.0, "cached_statements": 0},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _setup_sqlite_connection(dbapi_connection: Any, connection_record: Any) -> None:
        # Disable the driver's implicit BEGIN/COMMIT bookkeeping for the
        # lifetime of this pooled connection; SQLAlchemy now owns transactions.
        dbapi_connection.isolation_level = None
        # WAL is per database file and idempotent, and must be switched OUTSIDE
        # any transaction — this hook runs before the first one. The aiosqlite
        # adapter's cursor methods are coroutines: bridge via ``await_``.
        cursor = dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.await_(cursor.fetchall())
        dbapi_connection.await_(cursor.close())
        # Database-level write tracking for ensure_immediate_transaction (§6):
        # SQLite's authorizer reports every write action at PREPARE time —
        # immune to SQL string shapes (comment prefixes, WITH...UPDATE, writes
        # fired from triggers), which string-prefix matching is not. The flag
        # lives in the pool record's info, the SAME dict the begin hook resets
        # and the guard reads; SQLITE_OK only observes, never blocks.
        info = connection_record.info
        info[_TRANSACTION_HAS_DML] = False

        def _authorizer(
            action: int,
            arg1: str | None,
            arg2: str | None,
            db_name: str | None,
            trigger_name: str | None,
        ) -> int:
            if action in _WRITE_ACTIONS:
                info[_TRANSACTION_HAS_DML] = True
            return sqlite3.SQLITE_OK

        # SQLAlchemy's aiosqlite adapter does not proxy set_authorizer — go
        # to the driver connection (the aiosqlite.Connection) for it.
        driver = dbapi_connection.driver_connection
        dbapi_connection.await_(driver.set_authorizer(_authorizer))

    @event.listens_for(engine.sync_engine, "begin")
    def _emit_begin(conn: Connection) -> None:
        if conn.get_execution_options().get(BEGIN_IMMEDIATE_OPTION):
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            conn.exec_driver_sql("BEGIN")
        # Per-transaction write tracking (see ensure_immediate_transaction):
        # reset at every BEGIN; the authorizer marks actual write actions.
        conn.info[_TRANSACTION_HAS_DML] = False

    return engine
