"""P3-007 review: ``immediate_write_transaction`` ownership semantics.

The helper must behave like an honest transaction owner: the body's work is
committed ONLY on success, rolled back on ANY exception (including
cancellation — a half-finished write is never committed by teardown), and a
failed commit propagates instead of being swallowed. The ``finally`` block
only restores the deferred execution option.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from evoblue_video_mcp.storage import build_engine, init_db
from evoblue_video_mcp.storage.db import immediate_write_transaction
from evoblue_video_mcp.storage.models import AppSettings
from evoblue_video_mcp.storage.repository import _begin_immediate


@pytest.fixture
async def make_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[async_sessionmaker, Path]]:
    """One file-backed engine + session factory, disposed on teardown.

    The engine is OWNED here: a helper returning only the factory loses it,
    and a leaked engine leaves aiosqlite worker threads calling back into an
    already-closed event loop (PytestUnhandledThreadExceptionWarning) —
    timing-dependent, so single-file reruns cannot prove its absence."""
    db_path = tmp_path / "tx.db"
    engine = build_engine(db_path)
    await init_db(engine)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), db_path
    finally:
        await engine.dispose()


async def _read_scanned(sf: async_sessionmaker) -> int:
    async with sf() as sess:
        return int(
            (await sess.execute(text("SELECT scanned FROM index_status WHERE id = 1")))
            .scalar_one()
        )


async def test_success_commits_the_body(make_factory) -> None:
    sf, _ = make_factory
    async with sf() as sess, immediate_write_transaction(sess):
        await sess.execute(
            text("UPDATE index_status SET scanned = 7 WHERE id = 1")
        )
    assert await _read_scanned(sf) == 7


async def test_body_exception_rolls_back_and_propagates(make_factory) -> None:
    """Review P1: the teardown must NOT commit a half-finished write when the
    body raises — the old ``finally: commit()`` would have persisted it."""
    sf, _ = make_factory
    async with sf() as sess:
        with pytest.raises(RuntimeError, match="boom"):
            async with immediate_write_transaction(sess):
                await sess.execute(
                    text("UPDATE index_status SET scanned = 7 WHERE id = 1")
                )
                raise RuntimeError("boom")
    assert await _read_scanned(sf) == 0


async def test_cancellation_after_the_write_rolls_back(make_factory) -> None:
    """Review P1: cancellation landing AFTER the UPDATE but before the commit
    must roll back — never persist the half-finished operation."""
    sf, _ = make_factory
    started = asyncio.Event()

    async def cancelled_mid_write() -> None:
        async with sf() as sess, immediate_write_transaction(sess):
            await sess.execute(
                text("UPDATE index_status SET scanned = 7 WHERE id = 1")
            )
            started.set()
            await asyncio.sleep(3600)  # cancellation lands here

    task = asyncio.create_task(cancelled_mid_write())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _read_scanned(sf) == 0


async def test_guard_is_idempotent_inside_callers_immediate_transaction(
    make_factory,
) -> None:
    """P2: inside a caller's IMMEDIATE transaction the guard must return
    WITHOUT committing — the caller's pending write stays the caller's."""
    sf, _ = make_factory
    async with sf() as sess, immediate_write_transaction(sess):
        await sess.execute(text("UPDATE index_status SET scanned = 7 WHERE id = 1"))
        await _begin_immediate(sess)  # must be a no-op, NOT a commit
        # A separate reader (WAL reads never block) still sees the OLD value:
        assert await _read_scanned(sf) == 0
    # The caller's own commit (context exit) is what published the write.
    assert await _read_scanned(sf) == 7


async def test_guard_discards_readonly_deferred_snapshot(make_factory) -> None:
    """P2: a read-only deferred snapshot is discarded (rollback — reads are
    repeatable), then the write starts IMMEDIATE. This is the installer's
    verify-then-write pattern."""
    sf, _ = make_factory
    async with sf() as sess:
        await sess.execute(text("SELECT count(*) FROM index_status"))
        await _begin_immediate(sess)  # no silent commit; snapshot dropped
        await sess.execute(text("UPDATE index_status SET scanned = 5 WHERE id = 1"))
        await sess.commit()
    assert await _read_scanned(sf) == 5


async def test_guard_refuses_deferred_transaction_with_pending_changes(
    make_factory,
) -> None:
    """P2: pending unconfirmed caller writes are neither committed nor
    silently discarded — the guard refuses and the caller must resolve them."""
    sf, _ = make_factory
    async with sf() as sess:
        sess.add(AppSettings(id=1, setup_completed=True, updated_at=1.0))
        with pytest.raises(RuntimeError, match="refused"):
            await _begin_immediate(sess)
        await sess.rollback()
    async with sf() as check:
        row = (await check.execute(text("SELECT count(*) FROM app_settings"))).scalar_one()
    assert row == 0  # nothing leaked by the guard itself


async def test_guard_refuses_deferred_transaction_with_executed_core_dml(
    make_factory,
) -> None:
    """Review P1: Core DML sent via ``execute()`` is invisible to
    ``session.new/dirty/deleted`` — the engine-event tracking must catch it
    and REFUSE. The caller's own rollback then undoes the write; the guard
    never publishes it."""
    sf, _ = make_factory
    async with sf() as sess:
        await sess.execute(text("UPDATE index_status SET scanned = 91 WHERE id = 1"))
        with pytest.raises(RuntimeError, match="refused"):
            await _begin_immediate(sess)
        await sess.rollback()
    assert await _read_scanned(sf) == 0  # never published by the guard


async def test_guard_refuses_deferred_transaction_with_pending_delete(
    make_factory,
) -> None:
    """Review P1: ``session.deleted`` is part of the pending-write check —
    a pending ORM delete is refused, never flushed by the guard."""
    sf, _ = make_factory
    async with sf() as sess:
        sess.add(AppSettings(id=1, setup_completed=True, updated_at=1.0))
        await sess.commit()
        row = await sess.get(AppSettings, 1)
        assert row is not None
        await sess.delete(row)
        await sess.flush()  # the pending DELETE is now sent, uncommitted
        with pytest.raises(RuntimeError, match="refused"):
            await _begin_immediate(sess)
        await sess.rollback()
    async with sf() as check:
        row = (await check.execute(text("SELECT count(*) FROM app_settings"))).scalar_one()
    assert row == 1  # the delete was never published


async def test_commit_cancelled_after_durable_success_keeps_the_write(
    make_factory,
) -> None:
    """Review P1: a cancellation landing on the commit's await AFTER SQLite
    already made it durable must KEEP the write and propagate the cancel —
    restoring any pre-commit state here would manufacture a split."""
    sf, _ = make_factory
    async with sf() as sess:
        commit_started = asyncio.Event()
        release = asyncio.Event()
        real_commit = sess.commit

        async def durable_but_slow_commit():
            commit_started.set()
            await real_commit()  # DURABLY committed from here on
            await release.wait()  # the cancellation lands on THIS await

        sess.commit = durable_but_slow_commit  # type: ignore[method-assign]

        async def body() -> None:
            async with immediate_write_transaction(sess):
                await sess.execute(
                    text("UPDATE index_status SET scanned = 7 WHERE id = 1")
                )

        task = asyncio.create_task(body())
        await commit_started.wait()
        await asyncio.sleep(0.05)  # let the commit become durable
        task.cancel()
        release.set()  # let the shielded verdict resolve
        with pytest.raises(asyncio.CancelledError):
            await task
    assert await _read_scanned(sf) == 7  # the durable write is KEPT


async def test_commit_cancelled_and_failed_propagates_for_compensation(
    make_factory,
) -> None:
    """The failure twin: the commit's durable outcome is a FAILURE — the
    commit error propagates (the caller compensates against a database that
    truly did not adopt the value)."""
    import sqlalchemy.exc

    sf, _ = make_factory
    async with sf() as sess:
        commit_started = asyncio.Event()
        release = asyncio.Event()

        async def failing_commit():
            commit_started.set()
            await release.wait()
            raise sqlalchemy.exc.OperationalError("simulated commit failure",
                                                  None, None)

        sess.commit = failing_commit  # type: ignore[method-assign]

        async def body() -> None:
            async with immediate_write_transaction(sess):
                await sess.execute(
                    text("UPDATE index_status SET scanned = 7 WHERE id = 1")
                )

        task = asyncio.create_task(body())
        await commit_started.wait()
        task.cancel()
        release.set()
        with pytest.raises(sqlalchemy.exc.OperationalError):
            await task
    assert await _read_scanned(sf) == 0  # not committed → compensatable


async def test_verdict_survives_repeated_cancellation(make_factory) -> None:
    """Review P1: a SECOND cancel while the verdict is pending must not cancel
    the commit task — the whole wait is shielded, and only after the durable
    outcome is known does the cancellation propagate (with the write kept)."""
    sf, _ = make_factory
    async with sf() as sess:
        commit_started = asyncio.Event()
        release = asyncio.Event()
        real_commit = sess.commit
        verdict = {"cancelled": None}

        async def durable_but_slow_commit():
            commit_started.set()
            await real_commit()  # DURABLY committed
            try:
                await release.wait()
            except asyncio.CancelledError:
                verdict["cancelled"] = True
                raise
            verdict["cancelled"] = False

        sess.commit = durable_but_slow_commit  # type: ignore[method-assign]

        async def body() -> None:
            async with immediate_write_transaction(sess):
                await sess.execute(
                    text("UPDATE index_status SET scanned = 7 WHERE id = 1")
                )

        task = asyncio.create_task(body())
        await commit_started.wait()
        await asyncio.sleep(0.05)  # the commit becomes durable
        task.cancel()  # first cancel: lands on the outer shield
        await asyncio.sleep(0)
        task.cancel()  # SECOND cancel: lands on the verdict wait
        release.set()  # let the commit reach its verdict
        with pytest.raises(asyncio.CancelledError):
            await task
        # The verdict itself was never cancelled — repeated cancels were
        # absorbed by the shield, so the outcome is KNOWN (durable success).
        assert verdict["cancelled"] is False
    assert await _read_scanned(sf) == 7  # durable write KEPT


async def test_guard_refuses_same_sql_across_consecutive_transactions(
    make_factory,
) -> None:
    """Review P1: the sqlite3 module CACHES prepared statements per raw
    connection — a second execution of the SAME SQL string skips prepare, so
    the authorizer never fires and the guard misjudges the deferred
    transaction as read-only; its empty commit then publishes the write.

    Same session (same pooled connection), same SQL, three consecutive
    transactions: every round must be refused and the value must stay
    untouched. (Parameterized Core/ORM DML shares one SQL string across
    calls, so this is the common case, not an exotic one.)"""
    sf, _ = make_factory
    outcomes: list[str] = []
    async with sf() as sess:
        for _ in range(3):
            await sess.execute(
                text("UPDATE index_status SET scanned = 99 WHERE id = 1")
            )
            try:
                await _begin_immediate(sess)
            except RuntimeError:
                outcomes.append("refused")
                await sess.rollback()
            else:
                outcomes.append("MISSED")  # guard saw a "read-only" snapshot
                await sess.rollback()
        assert outcomes == ["refused", "refused", "refused"], outcomes
    assert await _read_scanned(sf) == 0  # never published by the guard


async def test_guard_refuses_schema_writes(make_factory) -> None:
    """Same failure class, adjacent hole: DDL inside a deferred session is a
    caller write too — an empty commit would publish the created table."""
    sf, db_path = make_factory
    async with sf() as sess:
        await sess.execute(text("SELECT count(*) FROM index_status"))
        await sess.execute(
            text("CREATE TABLE guard_probe_ddl (id INTEGER PRIMARY KEY)")
        )
        with pytest.raises(RuntimeError, match="refused"):
            await _begin_immediate(sess)
        await sess.rollback()
    import sqlite3 as driver

    raw = driver.connect(db_path)
    try:
        leaked = raw.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'guard_probe_ddl'"
        ).fetchone()[0]
    finally:
        raw.close()
    assert leaked == 0  # the DDL was never published


async def test_guard_refuses_writable_pragma(make_factory) -> None:
    """Review P2: a writable PRAGMA (``user_version``) is a persistent write
    the authorizer reports as SQLITE_PRAGMA — outside a DML/DDL-only action
    set it slips past the guard, whose empty commit then persists it. The
    action set is fail-closed: EVERY action that can change the database or
    the connection's persistent state is treated as a write (read pragmas
    included — refusing a read-only transaction is cheap, silently
    publishing an unowned write is not)."""
    sf, db_path = make_factory
    async with sf() as sess:
        await sess.execute(text("SELECT count(*) FROM index_status"))
        await sess.execute(text("PRAGMA user_version = 123"))
        with pytest.raises(RuntimeError, match="refused"):
            await _begin_immediate(sess)
        await sess.rollback()
    import sqlite3 as driver

    raw = driver.connect(db_path)
    try:
        version = raw.execute("PRAGMA user_version").fetchone()[0]
    finally:
        raw.close()
    assert version == 0  # never published by the guard


async def test_guard_refuses_comment_prefixed_update(make_factory) -> None:
    """Review P1: SQL string-prefix classification misses comment-prefixed
    writes — the database-level authorizer does not."""
    sf, _ = make_factory
    async with sf() as sess:
        await sess.execute(
            text("/* caller write */ UPDATE index_status SET scanned = 81 WHERE id = 1")
        )
        with pytest.raises(RuntimeError, match="refused"):
            await _begin_immediate(sess)
        await sess.rollback()
    assert await _read_scanned(sf) == 0  # never published


async def test_guard_refuses_cte_update(make_factory) -> None:
    """Review P1: ``WITH ... UPDATE`` is a write that starts with WITH —
    the authorizer catches it at PREPARE time."""
    sf, _ = make_factory
    async with sf() as sess:
        await sess.execute(
            text("WITH x AS (SELECT 1) UPDATE index_status SET scanned = 82 WHERE id = 1")
        )
        with pytest.raises(RuntimeError, match="refused"):
            await _begin_immediate(sess)
        await sess.rollback()
    assert await _read_scanned(sf) == 0  # never published


async def test_guard_refuses_writes_fired_from_triggers(tmp_path: Path) -> None:
    """Review P1: writes fired from INSIDE a trigger are database writes the
    statement text never mentions — the authorizer reports them too."""
    from evoblue_video_mcp.storage import build_engine, init_db

    db_path = tmp_path / "trigger.db"
    engine = build_engine(db_path)
    await init_db(engine)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE index_status_audit (scanned INTEGER)"))
        await conn.execute(
            text(
                "CREATE TRIGGER tr_audit AFTER UPDATE ON index_status "
                "BEGIN INSERT INTO index_status_audit VALUES (new.scanned); END"
            )
        )
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as sess:
            # A plain SELECT keeps the transaction read-only: no refusal.
            await sess.execute(text("SELECT count(*) FROM index_status"))
            await _begin_immediate(sess)
            await sess.rollback()
            # The UPDATE fires the trigger's INSERT — both are writes.
            await sess.execute(
                text("UPDATE index_status SET scanned = 3 WHERE id = 1")
            )
            with pytest.raises(RuntimeError, match="refused"):
                await _begin_immediate(sess)
            await sess.rollback()
        async with sf() as check:
            audited = (
                await check.execute(text("SELECT count(*) FROM index_status_audit"))
            ).scalar_one()
        assert audited == 0  # the write was never published
    finally:
        await engine.dispose()
