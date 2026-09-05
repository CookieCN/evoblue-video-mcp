"""P3-005 acceptance gates for the report index repository (P3-005 gates 3-7).

Contract: docs/FTS5_SCHEMA.md §5/§6 — pairing-atomic writes (savepoint-guarded),
no rowid drift, single-source FTS text, stale/missing semantics, open-issue
protocol, and entry invariant validation.
"""

import contextlib

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import evoblue_video_mcp.storage.report_repository as report_repository
from evoblue_video_mcp.storage.report_repository import (
    FtsBodyTexts,
    ReportIndexEntry,
    count_open_issues,
    get_index_status,
    get_report_document,
    list_open_issues,
    mark_report_missing,
    mark_report_stale,
    record_index_issue,
    record_rebuild_counters,
    resolve_index_issue,
    set_index_state,
    upsert_report_document,
    verify_fts,
)

NOW = 1756350000.0


def _entry(**overrides: object) -> ReportIndexEntry:
    values: dict[str, object] = {
        "job_id": "job-1",
        "analysis_id": "job-1",
        "title": "跨境电商选品策略完整指南",
        "platform": "youtube",
        "author": "Wilson",
        "video_id": "dQw4w9WgXcQ",
        "tags": ("选品", "跨境电商"),
        "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "published_at": None,
        "analyzed_at": NOW,
        "summary_mode": "auto",
        "relative_path": "跨境电商选品策略-00000000.md",
        "content_hash": "0" * 64,
        "byte_size": 1024,
    }
    values.update(overrides)
    return ReportIndexEntry(**values)  # type: ignore[arg-type]


def _body(**overrides: str) -> FtsBodyTexts:
    values: dict[str, str] = {
        "summary": "本期讲跨境电商的选品方法论。",
        "transcript": "大家好今天讲选品策略。",
    }
    values.update(overrides)
    return FtsBodyTexts(**values)


async def _fts_count(session: AsyncSession) -> int:
    return int((await session.execute(text("SELECT count(*) FROM report_fts"))).scalar_one())


async def _fts_rows(session: AsyncSession) -> list[tuple[int, str]]:
    rows = await session.execute(
        text("SELECT rowid, title FROM report_fts ORDER BY rowid")
    )
    return [(int(row[0]), str(row[1])) for row in rows]


async def test_upsert_inserts_paired_rows(session: AsyncSession) -> None:
    doc_id = await upsert_report_document(session, entry=_entry(), body=_body(), now=NOW)
    await session.commit()

    record = await get_report_document(session, job_id="job-1")
    assert record is not None
    assert record.id == doc_id
    assert record.doc_status == "active"
    assert record.doc_source == "pipeline"
    assert record.tags == ("选品", "跨境电商")
    assert record.relative_path == "跨境电商选品策略-00000000.md"

    # FTS row keyed by report_documents.id; title derived from the entry, not a
    # separate caller-supplied channel (contract §3 single source of truth).
    assert await _fts_rows(session) == [(doc_id, "跨 境 电 商 选 品 策 略 完 整 指 南")]
    verification = await verify_fts(session)
    assert verification.consistent
    assert (verification.document_count, verification.fts_row_count) == (1, 1)


async def test_update_reuses_id_and_replaces_fts_row(session: AsyncSession) -> None:
    first_id = await upsert_report_document(
        session, entry=_entry(), body=_body(), now=NOW
    )
    await session.commit()

    second_id = await upsert_report_document(
        session,
        entry=_entry(
            content_hash="1" * 64,
            relative_path="edited-11111111.md",
            title="用户编辑后的标题",  # entry change flows into FTS automatically
        ),
        body=_body(summary="用户编辑后的选品摘要。"),
        now=NOW + 10,
    )
    await session.commit()

    assert second_id == first_id  # gate 4: no rowid drift
    record = await get_report_document(session, job_id="job-1")
    assert record is not None
    assert record.content_hash == "1" * 64
    assert record.doc_status == "active"
    assert record.indexed_at == NOW + 10

    rows = await _fts_rows(session)
    assert len(rows) == 1 and rows[0][0] == first_id
    fts_row = (await session.execute(
        text("SELECT title, summary FROM report_fts WHERE rowid = :id"), {"id": first_id}
    )).mappings().one()
    assert fts_row["title"] == "用 户 编 辑 后 的 标 题"  # derived from entry, not a twin channel
    assert "编 辑 后" in fts_row["summary"]
    verification = await verify_fts(session)
    assert verification.consistent and not verification.fts_orphan_rows


async def test_rollback_discards_document_and_fts_together(
    session: AsyncSession,
) -> None:
    """Gate 5a: an abandoned transaction leaves neither table touched."""
    await upsert_report_document(session, entry=_entry(), body=_body(), now=NOW)
    await session.rollback()

    assert await get_report_document(session, job_id="job-1") is None
    assert await _fts_count(session) == 0
    verification = await verify_fts(session)
    assert verification.consistent
    assert (verification.document_count, verification.fts_row_count) == (0, 0)


async def test_midpair_failure_survives_outer_commit(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 5b: the savepoint protects against the real worker pattern — the
    exception is CAUGHT and the outer session then COMMITS a failure state;
    the half-written pair must not land."""
    await upsert_report_document(session, entry=_entry(), body=_body(), now=NOW)
    await session.commit()

    def _explode(entry: ReportIndexEntry, body: FtsBodyTexts) -> dict[str, str]:
        raise RuntimeError("injected failure between paired writes")

    monkeypatch.setattr(report_repository, "_fts_row_values", _explode)
    # The worker swallows the handler failure and keeps using the session.
    with contextlib.suppress(RuntimeError):
        await upsert_report_document(
            session,
            entry=_entry(job_id="job-2", analysis_id="job-2"),
            body=_body(),
            now=NOW,
        )
    monkeypatch.undo()
    await session.commit()  # e.g. mark_failure() persisting the failed status

    assert await get_report_document(session, job_id="job-1") is not None
    assert await get_report_document(session, job_id="job-2") is None
    assert await _fts_count(session) == 1
    assert (await verify_fts(session)).consistent


async def test_stale_keeps_fts_row_and_missing_drops_it(session: AsyncSession) -> None:
    """Gate 6: the three doc_status semantics from contract §5/§6."""
    doc_id = await upsert_report_document(session, entry=_entry(), body=_body(), now=NOW)
    await session.commit()

    assert await mark_report_stale(session, job_id="job-1", now=NOW + 1)
    await session.commit()
    record = await get_report_document(session, job_id="job-1")
    assert record is not None and record.doc_status == "stale"
    assert await _fts_count(session) == 1  # stale stays searchable
    assert (await verify_fts(session)).consistent  # stale counts toward the check

    assert await mark_report_stale(session, job_id="job-1", now=NOW + 2) is False  # idempotent

    assert await mark_report_missing(session, job_id="job-1", now=NOW + 3)
    await session.commit()
    record = await get_report_document(session, job_id="job-1")
    assert record is not None and record.doc_status == "missing"
    assert await _fts_count(session) == 0  # missing drops its FTS row
    verification = await verify_fts(session)
    assert verification.consistent
    assert (verification.document_count, verification.fts_row_count) == (0, 0)

    assert await mark_report_missing(session, job_id="job-1", now=NOW + 4) is False
    # missing → stale is illegal: its FTS row is gone, so the flip would trip
    # verify_fts immediately. Recovery is a full re-index, never a status edit.
    assert await mark_report_stale(session, job_id="job-1", now=NOW + 4) is False
    await session.commit()
    record = await get_report_document(session, job_id="job-1")
    assert record is not None and record.doc_status == "missing"
    assert (await verify_fts(session)).consistent

    # Re-indexing a missing document restores it to active with a fresh FTS row.
    await upsert_report_document(session, entry=_entry(), body=_body(), now=NOW + 5)
    await session.commit()
    record = await get_report_document(session, job_id="job-1")
    assert record is not None and record.doc_status == "active"
    assert record.id == doc_id
    assert (await verify_fts(session)).consistent


def test_entry_invariants_reject_invalid_records() -> None:
    """P2 gate: frozen invariants are enforced at the boundary, not in the DDL."""
    with pytest.raises(ValueError, match="analysis_id == job_id"):
        _entry(analysis_id="different")
    with pytest.raises(ValueError, match="doc_source"):
        _entry(doc_source="import")
    with pytest.raises(ValueError, match="content_hash"):
        _entry(content_hash="xyz")
    with pytest.raises(ValueError, match="summary_preview"):
        _entry(summary_preview="长" * 201)
    with pytest.raises(ValueError, match="relative_path"):
        _entry(relative_path="")
    with pytest.raises(ValueError, match="timestamps"):
        _entry(analyzed_at=0)
    with pytest.raises(ValueError, match="summary_mode"):
        _entry(summary_mode="深度解读")
    with pytest.raises(ValueError, match="summary_mode"):
        _entry(summary_mode="")  # v8 table has no legacy rows to accommodate
    with pytest.raises(ValueError, match="source_url"):
        _entry(source_url="ftp://example.com/video")
    with pytest.raises(ValueError, match="source_url"):
        _entry(source_url="not-a-url")
    with pytest.raises(ValueError, match="platform"):
        _entry(platform="YouTube")
    with pytest.raises(ValueError, match="timestamps"):
        _entry(analyzed_at=float("nan"))
    with pytest.raises(ValueError, match="timestamps"):
        _entry(published_at=float("inf"))
    # Valid construction still works (defaults pass all invariants).
    assert _entry().doc_source == "pipeline"
    assert _entry(summary_mode="unboxing", platform="bilibili").summary_mode == "unboxing"


async def test_open_issue_dedup_resolve_recurrence(session: AsyncSession) -> None:
    """Gate 7: update → resolve → recurrence-insert, backed by uq_index_issues_open."""
    first = await record_index_issue(
        session,
        issue_code="MD_MISSING_FIELD",
        relative_path="broken.md",
        detail="missing title",
        now=NOW,
    )
    second = await record_index_issue(
        session,
        issue_code="MD_MISSING_FIELD",
        relative_path="broken.md",
        detail="missing title (again)",
        now=NOW + 5,
    )
    await session.commit()
    assert first == second  # deduplicated onto the open row
    open_issues = await list_open_issues(session, issue_code="MD_MISSING_FIELD")
    assert len(open_issues) == 1 and open_issues[0].last_seen_at == NOW + 5
    assert open_issues[0].detail == "missing title"  # first detail preserved
    assert await count_open_issues(session) == 1

    assert await resolve_index_issue(
        session, issue_code="MD_MISSING_FIELD", relative_path="broken.md", now=NOW + 10
    ) == 1
    assert await resolve_index_issue(
        session, issue_code="MD_MISSING_FIELD", relative_path="broken.md", now=NOW + 11
    ) == 0
    await session.commit()
    assert await count_open_issues(session) == 0

    recurrence = await record_index_issue(
        session,
        issue_code="MD_MISSING_FIELD",
        relative_path="broken.md",
        detail="recurred",
        now=NOW + 20,
    )
    await session.commit()
    assert recurrence != first  # new row after resolution
    issues = await list_open_issues(session, issue_code="MD_MISSING_FIELD")
    assert len(issues) == 1 and issues[0].id == recurrence
    assert issues[0].detail == "recurred"
    assert await count_open_issues(session) == 1


async def test_set_index_state_transitions(session: AsyncSession) -> None:
    snapshot = await get_index_status(session)
    assert snapshot.state == "idle"  # seeded by migration v8
    assert snapshot.started_at is None and snapshot.finished_at is None

    await set_index_state(session, state="running", now=NOW)
    await record_rebuild_counters(session, scanned=3, indexed=2, unchanged=1)
    await set_index_state(session, state="idle", now=NOW + 9)
    await session.commit()

    snapshot = await get_index_status(session)
    assert snapshot.state == "idle"
    assert snapshot.started_at == NOW and snapshot.finished_at == NOW + 9
    assert (snapshot.scanned, snapshot.indexed, snapshot.unchanged) == (3, 2, 1)

    await set_index_state(
        session, state="needs_rebuild", now=NOW + 10, error_code="FTS_DRIFT"
    )
    await session.commit()
    snapshot = await get_index_status(session)
    assert snapshot.state == "needs_rebuild"
    assert snapshot.last_error_code == "FTS_DRIFT"
    assert snapshot.scanned == 3  # needs_rebuild keeps counters for diagnosis

    with pytest.raises(ValueError, match="unknown index state"):
        await set_index_state(session, state="bogus", now=NOW)  # type: ignore[arg-type]
