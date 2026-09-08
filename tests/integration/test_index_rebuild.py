"""P3-007 acceptance: the deterministic rescan engine (docs/INDEX_REBUILD.md §7).

Covers the frozen acceptance items: delete-SQLite self-heal end to end, user
edits honored, quarantine isolation, duplicate ownership convergence, crash
orphan recovery, never writing user files, purge semantics, and the manual
API trigger.
"""

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.reports.renderer import render_markdown
from evoblue_video_mcp.reports.schema import ReportDocument
from evoblue_video_mcp.storage import build_engine, init_db
from evoblue_video_mcp.storage.fts import build_match_query
from evoblue_video_mcp.storage.rebuild import IndexRebuildService
from evoblue_video_mcp.storage.report_repository import (
    FtsBodyTexts,
    ReportIndexEntry,
    count_open_issues,
    get_index_status,
    get_report_document,
    list_open_issues,
    list_report_documents,
    search_reports,
    set_index_state,
    upsert_report_document,
    verify_fts,
)
from evoblue_video_mcp.storage.repository import save_app_settings

# P7 review (runnable-setup invariant): settings PUTs on a COMPLETED record
# must pass the same runnable gate as the worker claim. These five pointer-
# compensation tests seed a completed record and drive the endpoint, so they
# seed a runnable LLM config and hand the app a working credential store.
_RUNNABLE_LLM_KWARGS = {
    "llm_provider": "deepseek",
    "llm_base_url": "https://api.deepseek.com",
    "llm_model": "deepseek-chat",
    "llm_credential_ref": "llm:deepseek",
}


class _RunnableCredentials:
    def get_secret(self, reference: str) -> str | None:
        return "sk-test" if reference == "llm:deepseek" else None


def _markdown(*, job_id: str, title: str, summary: str) -> str:
    doc = ReportDocument(
        analysis_id=job_id,
        source_url=f"https://www.youtube.com/watch?v={job_id}",
        platform="youtube",
        video_id=job_id,
        title=title,
        analyzed_at=datetime(2026, 1, 1, tzinfo=UTC),
        summary_mode="auto",
        tags=["选品"],
        core_summary=summary,
    )
    return render_markdown(doc)


def _write(
    root: Path, rel: str, *, job_id: str | None = None, title: str | None = None,
    summary: str = "本期讲跨境电商的选品方法论。", content: str | None = None,
) -> str:
    """Write one report file; returns the file bytes' sha-256.

    Uses ``write_bytes`` to mirror ReportWriter (LF, no newline translation).
    """
    text = content if content is not None else _markdown(
        job_id=job_id or rel, title=title or rel, summary=summary
    )
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _tree_hash(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class _Env:
    """One database + report directory + rebuild service."""

    def __init__(self, tmp_path: Path, name: str) -> None:
        self.db_path = tmp_path / f"{name}.db"
        self.report_dir = tmp_path / f"reports-{name}"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.engine = build_engine(self.db_path)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.service = IndexRebuildService(self.session_factory)

    async def setup(self) -> "_Env":
        await init_db(self.engine)
        async with self.session_factory() as sess:
            await save_app_settings(
                sess,
                setup_completed=True,
                now=1000.0,
                report_directory=str(self.report_dir),
            )
        return self

    async def dispose(self) -> None:
        await self.service.shutdown()
        await self.engine.dispose()

    async def destroy_database(self) -> None:
        await self.engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.db_path) + suffix)
            if candidate.exists():
                candidate.unlink()
        # A deleted database still needs its schema on next boot.
        self.engine = build_engine(self.db_path)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.service = IndexRebuildService(self.session_factory)
        await init_db(self.engine)
        async with self.session_factory() as sess:
            await save_app_settings(
                sess,
                setup_completed=True,
                now=1000.0,
                report_directory=str(self.report_dir),
            )


@pytest.fixture
async def env(tmp_path: Path):
    e = await _Env(tmp_path, "main").setup()
    yield e
    await e.dispose()


async def _snapshot_search(
    session_factory: async_sessionmaker[AsyncSession], q: str
) -> list[str]:
    match = build_match_query(q)
    or_query = match.replace(" AND ", " OR ")
    async with session_factory() as sess:
        hits, _ = await search_reports(sess, match_query=match, or_query=or_query)
    return [hit.job_id for hit in hits]


async def test_delete_sqlite_then_restart_recovers_searchable_history(
    tmp_path: Path,
) -> None:
    """§7.1 — the PRD P3 acceptance, end to end."""
    env = await _Env(tmp_path, "before").setup()
    try:
        for i in range(3):
            _write(env.report_dir, f"doc{i}.md", job_id=f"job-{i}", title=f"视频{i}")
        outcome = await env.service.run_rebuild()
        assert outcome.indexed == 3 and outcome.consistent
    finally:
        await env.dispose()
    # Delete the database file entirely, then "restart" the Engine.
    after = await _Env(tmp_path, "before").setup()
    await after.destroy_database()
    try:
        assert (await after.service._startup_needs_rebuild()) is True
        await after.service.reconcile_on_startup()
        async with after.session_factory() as sess:
            records, total = await list_report_documents(sess)
            assert total == 3
            assert {r.job_id for r in records} == {"job-0", "job-1", "job-2"}
            assert all(r.doc_source == "rebuild" for r in records)
            assert all(r.doc_status == "active" for r in records)
            verification = await verify_fts(sess)
            assert verification.consistent
        hits = await _snapshot_search(after.session_factory, "选品")
        assert len(hits) == 3
        async with after.session_factory() as sess:
            snapshot = await get_index_status(sess)
        assert snapshot.state == "idle"
    finally:
        await after.dispose()


async def test_user_edit_is_honored_by_rebuild(env: _Env) -> None:
    """§7.2 — edited file wins; search reflects the edit; hash updated."""
    _write(env.report_dir, "a.md", job_id="job-a", title="视频A")
    await env.service.run_rebuild()
    _write(
        env.report_dir, "a.md", job_id="job-a", title="视频A",
        summary="用户编辑后的选品洞察。",
    )
    outcome = await env.service.run_rebuild()
    assert outcome.indexed == 1 and outcome.unchanged == 0
    async with env.session_factory() as sess:
        record = await get_report_document(sess, job_id="job-a")
        assert record is not None
        assert record.summary_preview.startswith("用户编辑后的选品洞察")
        assert record.doc_status == "active"
    hits = await _snapshot_search(env.session_factory, "用户编辑")
    assert hits == ["job-a"]


async def test_corrupt_file_is_quarantined_without_side_effects(env: _Env) -> None:
    """§7.3 — broken frontmatter quarantined; siblings unaffected; no writes."""
    good_hash = _write(env.report_dir, "good.md", job_id="job-g", title="好文档")
    _write(env.report_dir, "broken.md", content="这不是 frontmatter 开头的文件\n")
    before_tree = _tree_hash(env.report_dir)
    outcome = await env.service.run_rebuild()
    assert outcome.indexed == 1 and outcome.quarantined == 1
    assert _tree_hash(env.report_dir) == before_tree
    async with env.session_factory() as sess:
        assert await get_report_document(sess, job_id="job-g") is not None
        from evoblue_video_mcp.storage.report_repository import count_open_issues

        assert await count_open_issues(sess) == 1
        snapshot = await get_index_status(sess)
        assert snapshot.state == "idle"
    hits = await _snapshot_search(env.session_factory, "选品")
    assert hits == ["job-g"]
    # The good file's hash is exactly what we wrote — engine never rewrote it.
    assert (
        hashlib.sha256((env.report_dir / "good.md").read_bytes()).hexdigest()
        == good_hash
    )


async def test_duplicate_analysis_id_converges_across_db_histories(
    tmp_path: Path,
) -> None:
    """§7.4/§7.12 — winner = sorted-min path of the file set, regardless of
    whether the database survived."""
    # Scenario A: b.md indexed first (pipeline history), a.md appears later.
    env_a = await _Env(tmp_path, "conv-a").setup()
    try:
        _write(
            env_a.report_dir, "sub/b.md", job_id="same-id", title="重复文档",
            summary="B 版本内容。",
        )
        await env_a.service.run_rebuild()
        async with env_a.session_factory() as sess:
            record = await get_report_document(sess, job_id="same-id")
        assert record is not None and record.relative_path == "sub/b.md"
        _write(
            env_a.report_dir, "a.md", job_id="same-id", title="重复文档",
            summary="A 版本内容。",
        )
        await env_a.service.run_rebuild()
        # Same winner + losing-duplicate audit as the fresh-database scenario.
        async with env_a.session_factory() as sess:
            record = await get_report_document(sess, job_id="same-id")
            assert record is not None
            assert record.relative_path == "a.md"
            from evoblue_video_mcp.storage.report_repository import count_open_issues

            assert await count_open_issues(sess) == 1  # b.md duplicate stays open
    finally:
        await env_a.dispose()

    # Scenario B: fresh database, both files present from the start.
    env_b = await _Env(tmp_path, "conv-b").setup()
    try:
        _write(
            env_b.report_dir, "sub/b.md", job_id="same-id", title="重复文档",
            summary="B 版本内容。",
        )
        _write(
            env_b.report_dir, "a.md", job_id="same-id", title="重复文档",
            summary="A 版本内容。",
        )
        await env_b.service.run_rebuild()
        async with env_b.session_factory() as sess:
            record = await get_report_document(sess, job_id="same-id")
            assert record is not None
            assert record.relative_path == "a.md"
            from evoblue_video_mcp.storage.report_repository import count_open_issues

            assert await count_open_issues(sess) == 1  # b.md duplicate stays open
    finally:
        await env_b.dispose()


async def test_orphaned_running_state_self_heals_on_startup(
    tmp_path: Path,
) -> None:
    """§7.5/§7.11 — a persisted ``running`` from a killed process is orphaned
    at boot (no heartbeat grace) and the automatic re-run converges."""
    env = await _Env(tmp_path, "crash").setup()
    try:
        _write(env.report_dir, "a.md", job_id="job-a", title="视频A")
        _write(env.report_dir, "b.md", job_id="job-b", title="视频B")
        async with env.session_factory() as sess:
            await set_index_state(sess, state="running", now=time.time())
            await sess.commit()
        # Simulate a 3-second restart: reconciliation runs at boot.
        await env.service.reconcile_on_startup()
        async with env.session_factory() as sess:
            snapshot = await get_index_status(sess)
        assert snapshot.state == "idle"
        assert snapshot.last_error_code is None
        async with env.session_factory() as sess:
            verification = await verify_fts(sess)
        assert verification.consistent
        assert (verification.document_count, verification.fts_row_count) == (2, 2)
    finally:
        await env.dispose()


async def test_rebuild_never_writes_markdown_files(env: _Env) -> None:
    """§7.6 — the scan is read-only against user files, including conflict
    copies and nested directories."""
    _write(env.report_dir, "sub/nested.md", job_id="job-n", title="嵌套文档")
    _write(
        env.report_dir, "sub/nested.conflict-1a2b3c4d.md",
        job_id="job-n", title="嵌套文档", summary="冲突副本内容。",
    )
    (env.report_dir / ".hidden").mkdir()
    _write(env.report_dir, ".hidden/skip.md", job_id="job-h", title="跳过")
    _write(env.report_dir, "notes.txt", content="not markdown")
    before = _tree_hash(env.report_dir)
    outcome = await env.service.run_rebuild()
    # both files carry analysis_id job-n: sorted-first (conflict copy) wins,
    # nested.md becomes the losing duplicate
    assert outcome.indexed == 1 and outcome.duplicates == 1
    assert _tree_hash(env.report_dir) == before
    hits = await _snapshot_search(env.session_factory, "冲突副本")
    assert hits == ["job-n"]


async def test_missing_files_marked_missing_and_purge_deletes(env: _Env) -> None:
    """§5 — default keeps an audit record (missing); explicit purge deletes."""
    _write(env.report_dir, "kept.md", job_id="job-keep", title="保留")
    _write(env.report_dir, "gone.md", job_id="job-gone", title="消失")
    await env.service.run_rebuild()
    (env.report_dir / "gone.md").unlink()

    outcome = await env.service.run_rebuild()
    assert outcome.removed == 1 and outcome.purged == 0
    async with env.session_factory() as sess:
        record = await get_report_document(sess, job_id="job-gone")
        assert record is not None and record.doc_status == "missing"
    hits = await _snapshot_search(env.session_factory, "消失")
    assert hits == []  # missing docs are out of search

    outcome = await env.service.run_rebuild(purge=True)
    assert outcome.purged == 1
    async with env.session_factory() as sess:
        assert await get_report_document(sess, job_id="job-gone") is None


async def test_unchanged_files_skip_and_issue_resolves(env: _Env) -> None:
    """§2 unchanged classification + §3 issue resolution on fix."""
    _write(env.report_dir, "a.md", job_id="job-a", title="视频A")
    _write(env.report_dir, "bad.md", content="broken")
    first = await env.service.run_rebuild()
    assert first.indexed == 1 and first.quarantined == 1

    _write(env.report_dir, "bad.md", job_id="job-bad", title="已修复")
    fixed = await env.service.run_rebuild()
    assert fixed.indexed == 1 and fixed.quarantined == 0

    async with env.session_factory() as sess:
        assert await count_open_issues(sess) == 0

    steady = await env.service.run_rebuild()
    assert steady.indexed == 0 and steady.unchanged == 2 and steady.quarantined == 0


async def test_manual_rebuild_api_trigger(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """§5 — POST /api/index/rebuild: 202 + running state; 409 on concurrency;
    503 envelope when the service is absent."""
    from evoblue_video_mcp.web import create_app

    report_dir = tmp_path / "reports"
    report_dir.mkdir(exist_ok=True)
    async with session_factory() as sess:
        await save_app_settings(
            sess, setup_completed=True, now=1000.0, report_directory=str(report_dir)
        )
    service = IndexRebuildService(session_factory)
    app = create_app(session_factory=session_factory, index_rebuild_service=service)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    async with client:
        resp = await client.post("/api/index/rebuild", json={"purge_missing": False})
        assert resp.status_code == 202
        assert resp.json() == {"state": "running"}
        # Wait for the background rebuild to finish.
        for _ in range(200):
            async with session_factory() as sess:
                snapshot = await get_index_status(sess)
            if snapshot.state == "idle":
                break
            await asyncio.sleep(0.01)
        assert snapshot.state == "idle"

        # Hold the in-process lock: the endpoint must answer 409.
        async with service._lock:
            conflict = await client.post("/api/index/rebuild", json={})
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "REBUILD_ALREADY_RUNNING"

    # A build without the service answers 503 in the frozen envelope.
    bare = create_app(session_factory=session_factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=bare), base_url="http://t"
    ) as bare_client:
        resp = await bare_client.post("/api/index/rebuild", json={})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SEARCH_INDEX_UNAVAILABLE"


async def test_startup_orphan_resolves_via_api_surface(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """The startup path parks needs_rebuild and auto-runs to idle with files."""
    report_dir = tmp_path / "reports"
    report_dir.mkdir(exist_ok=True)
    _write(report_dir, "a.md", job_id="job-a", title="视频A")
    async with session_factory() as sess:
        await save_app_settings(
            sess, setup_completed=True, now=1000.0, report_directory=str(report_dir)
        )
        await sess.execute(
            text("UPDATE index_status SET state = 'running' WHERE id = 1")
        )
        await sess.commit()
    service = IndexRebuildService(session_factory)
    await service.reconcile_on_startup()
    async with session_factory() as sess:
        snapshot = await get_index_status(sess)
        verification = await verify_fts(sess)
    assert snapshot.state == "idle"
    assert verification.consistent
    assert verification.document_count == 1
NOW = 1756350000.0


async def test_production_lifespan_self_heals_after_database_deletion(
    tmp_path: Path,
) -> None:
    """Review P1-1 acceptance: the REAL production lifecycle. Reports exist in
    the default reports dir; the DB is deleted; the restarted Engine self-heals
    WITHOUT the test touching app_settings — the fallback directory injected by
    bootstrap is the authoritative pointer."""
    from evoblue_video_mcp.config import Settings
    from evoblue_video_mcp.runtime.bootstrap import create_runtime_app

    data_dir = tmp_path / "data"
    reports = data_dir / "reports"  # bootstrap's default_reports = data/reports
    reports.mkdir(parents=True)
    for i in range(3):
        _write(reports, f"doc{i}.md", job_id=f"job-{i}", title=f"视频{i}")

    class _NoCredentials:
        def get_secret(self, reference: str) -> str | None:
            return None

    async def _no_handlers(settings: object) -> None:
        return None

    settings = Settings(data_directory=data_dir)

    async def run_pass() -> dict[str, object]:
        app = create_runtime_app(
            settings,
            credential_store=_NoCredentials(),
            handler_factory=_no_handlers,
        )
        async with app.router.lifespan_context(app):
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            )
            async with client:
                # The startup reconciliation runs as a background task; poll the
                # BUSINESS result (not the state — the seeded "idle" precedes it).
                history: dict[str, object] = {}
                for _ in range(500):
                    history = (await client.get("/api/history")).json()
                    if history["total"] == 3:
                        break
                    await asyncio.sleep(0.02)
                assert history["total"] == 3, history
                # the tail of the rebuild (absence pass + verify + idle) follows
                status: dict[str, object] = {}
                for _ in range(500):
                    status = (await client.get("/api/index/status")).json()
                    if status["state"] == "idle":
                        break
                    await asyncio.sleep(0.02)
                assert status["state"] == "idle", status
                search = (await client.get("/api/search", params={"q": "选品"})).json()
                # Review P1-1: the recovered index must be fully READABLE —
                # history detail and report sections go through the SAME §4
                # resolver as the rebuild (the settings row is NULL here).
                detail = (await client.get("/api/history/job-1")).json()
                assert detail["core_summary"] and "选品" in detail["core_summary"], detail
                summary = await client.get(
                    "/api/history/job-1/report", params={"section": "summary"}
                )
                assert summary.status_code == 200
                assert summary.json()["markdown"].startswith("## 核心摘要")
                assert "选品" in summary.json()["markdown"]
                metadata = await client.get(
                    "/api/history/job-1/report", params={"section": "metadata"}
                )
                assert metadata.status_code == 200
                assert "analysis_id" in metadata.json()["markdown"]
                full = await client.get(
                    "/api/history/job-1/report", params={"section": "full"}
                )
                assert full.status_code == 200
                assert "## 核心摘要" in full.json()["markdown"]
                transcript = await client.get(
                    "/api/history/job-1/report", params={"section": "transcript"}
                )
                # 404 SECTION_NOT_AVAILABLE proves the file was read AND parsed
                # (not REPORT_READ_FAILED / 500): the section is merely absent.
                assert transcript.status_code == 404
                assert transcript.json()["error"]["code"] == "SECTION_NOT_AVAILABLE"
            return {"total": history["total"], "hits": len(search["items"])}

    first = await run_pass()
    assert first == {"total": 3, "hits": 3}

    # Simulate the crash-with-deleted-database: remove the SQLite file. The
    # test must NOT rewrite app_settings — production has no such step.
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(data_dir / "evoblue.db") + suffix)
        if candidate.exists():
            candidate.unlink()

    second = await run_pass()
    assert second == {"total": 3, "hits": 3}

    # Prove the recovery did not rely on a settings row: report_directory is
    # still NULL in the fresh database (the fallback dir did the work).
    import sqlite3

    conn = sqlite3.connect(str(data_dir / "evoblue.db"))
    try:
        row = conn.execute("SELECT report_directory FROM app_settings").fetchone()
        assert row is None or row[0] is None
    finally:
        conn.close()


async def test_root_unavailable_fails_safe_and_never_wipes_index(tmp_path: Path) -> None:
    """Review P1-1 fail-safe: without any configured directory the rebuild must
    FAIL (needs_rebuild) — it must never treat the missing root as an empty
    directory and wipe existing documents via the absence pass."""
    from evoblue_video_mcp.storage.rebuild import RebuildRootUnavailable

    env = await _Env(tmp_path, "rootless").setup()
    try:
        # Clear the settings pointer: no report_directory anywhere (settings
        # and fallback both empty) — the fail-safe precondition.
        async with env.session_factory() as sess:
            await save_app_settings(
                sess, setup_completed=True, now=NOW, report_directory=None
            )
        # An indexed document WITHOUT any report_directory setting anywhere.
        content = _markdown(job_id="job-x", title="文档X", summary="选品内容。")
        data = content.encode("utf-8")
        async with env.session_factory() as sess:
            await upsert_report_document(
                sess,
                entry=ReportIndexEntry(
                    job_id="job-x",
                    analysis_id="job-x",
                    title="文档X",
                    platform="youtube",
                    author="Wilson",
                    video_id="x",
                    source_url="https://www.youtube.com/watch?v=x",
                    published_at=None,
                    analyzed_at=NOW,
                    summary_mode="auto",
                    relative_path="x.md",
                    content_hash=hashlib.sha256(data).hexdigest(),
                ),
                body=FtsBodyTexts(summary="选品内容。", transcript=""),
                now=NOW,
            )
            await sess.commit()
        service = IndexRebuildService(env.session_factory)  # no fallback root
        with pytest.raises(RebuildRootUnavailable):
            await service.run_rebuild()
        async with env.session_factory() as sess:
            snapshot = await get_index_status(sess)
            assert snapshot.state == "needs_rebuild"
            assert snapshot.last_error_code == "ROOT_UNAVAILABLE"
            record = await get_report_document(sess, job_id="job-x")
            # untouched: the absence pass never ran
            assert record is not None and record.doc_status == "active"
            verification = await verify_fts(sess)
            assert verification.consistent
    finally:
        await env.dispose()


async def test_start_rebuild_back_to_back_single_winner(env: _Env) -> None:
    """Review P1-3: no await between the two calls — the synchronously-set
    scheduled flag must reject the second invocation."""
    _write(env.report_dir, "a.md", job_id="job-a", title="视频A")
    assert await env.service.start_rebuild() is True
    assert await env.service.start_rebuild() is False
    async with env.session_factory() as sess:
        snapshot = await get_index_status(sess)
    for _ in range(500):
        async with env.session_factory() as sess:
            snapshot = await get_index_status(sess)
        if snapshot.state == "idle":
            break
        await asyncio.sleep(0.01)
    assert snapshot.state == "idle"


async def test_concurrent_http_rebuild_yields_one_202(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Review P1-3: two simultaneous HTTP triggers → exactly one 202."""
    from evoblue_video_mcp.web import create_app

    report_dir = tmp_path / "reports"
    report_dir.mkdir(exist_ok=True)
    _write(report_dir, "a.md", job_id="job-a", title="视频A")
    async with session_factory() as sess:
        await save_app_settings(
            sess, setup_completed=True, now=1000.0, report_directory=str(report_dir)
        )
    service = IndexRebuildService(session_factory)
    app = create_app(session_factory=session_factory, index_rebuild_service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        responses = await asyncio.gather(
            client.post("/api/index/rebuild", json={}),
            client.post("/api/index/rebuild", json={}),
        )
    codes = sorted(r.status_code for r in responses)
    assert codes == [202, 409]
    for _ in range(500):
        async with session_factory() as sess:
            snapshot = await get_index_status(sess)
        if snapshot.state == "idle":
            break
        await asyncio.sleep(0.01)
    assert snapshot.state == "idle"
    # Let the accepted background task finish and release its session before
    # the engine fixture is disposed (else aiosqlite GC warnings fire).
    await service.shutdown()


async def test_absence_pass_unclaims_corrupted_and_reidentified_files(
    env: _Env,
) -> None:
    """Review P1-2: scanned paths that fail to claim this run (corrupted, or
    re-identified with a new analysis_id) must exit history and search — their
    stale rows become missing instead of staying active."""
    _write(env.report_dir, "corrupt.md", job_id="job-c", title="将损坏的文档")
    _write(env.report_dir, "moved.md", job_id="job-old", title="将改ID的文档")
    await env.service.run_rebuild()

    _write(env.report_dir, "corrupt.md", content="broken beyond repair")
    _write(
        env.report_dir, "moved.md", job_id="job-new", title="改ID后的文档",
        summary="全新身份的内容。",
    )
    outcome = await env.service.run_rebuild()
    assert outcome.indexed == 1 and outcome.removed >= 1
    async with env.session_factory() as sess:
        old_corrupt = await get_report_document(sess, job_id="job-c")
        assert old_corrupt is not None and old_corrupt.doc_status == "missing"
        old_moved = await get_report_document(sess, job_id="job-old")
        assert old_moved is not None and old_moved.doc_status == "missing"
        new_doc = await get_report_document(sess, job_id="job-new")
        assert new_doc is not None and new_doc.doc_status == "active"
    hits = await _snapshot_search(env.session_factory, "将损坏的文档")
    assert "job-c" not in hits  # stale content out of search


async def test_empty_body_warning_lifecycle(env: _Env) -> None:
    """Review P2-4: MD_EMPTY_BODY is recorded as a warning (still indexed) and
    resolved once the body gains analysis content."""

    _write(env.report_dir, "empty.md", job_id="job-e", title="空正文文档", summary="")
    outcome = await env.service.run_rebuild()
    assert outcome.indexed == 1
    async with env.session_factory() as sess:
        assert await count_open_issues(sess) == 1

    _write(
        env.report_dir, "empty.md", job_id="job-e", title="空正文文档",
        summary="补上正文后的选品摘要。",
    )
    await env.service.run_rebuild()
    async with env.session_factory() as sess:
        assert await count_open_issues(sess) == 0


async def test_duplicate_issue_detail_and_reform_resolution(env: _Env) -> None:
    """Review P2-5: the duplicate diagnostic carries both paths, analyzed_at
    and hashes; a reformed loser (now a unique ID) resolves its old issue."""

    # sorted-min path wins: "aa-winner.md" < "zz-loser.md"
    _write(env.report_dir, "aa-winner.md", job_id="dup-1", title="重复文档")
    _write(
        env.report_dir, "zz-loser.md", job_id="dup-1", title="重复文档",
        summary="败者内容。",
    )
    outcome = await env.service.run_rebuild()
    assert outcome.indexed == 1 and outcome.duplicates == 1
    async with env.session_factory() as sess:
        issues = await list_open_issues(sess)
    detail = issues[0].detail
    assert detail is not None
    assert "aa-winner.md" in detail and "zz-loser.md" in detail
    assert "analyzed_at" in detail and "sha256" in detail
    assert issues[0].issue_code == "DUPLICATE_ANALYSIS_ID"

    # The loser becomes a unique document → its old duplicate issue resolves.
    _write(
        env.report_dir, "zz-loser.md", job_id="unique-1", title="改ID后的败者",
        summary="唯一身份的新内容。",
    )
    await env.service.run_rebuild()
    async with env.session_factory() as sess:
        assert await count_open_issues(sess) == 0
        record = await get_report_document(sess, job_id="unique-1")
        assert record is not None and record.doc_status == "active"


async def test_http_rebuild_without_root_answers_503_envelope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Review P1-A: the missing-root failure must surface as a real HTTP 503
    envelope — the endpoint pre-flights the root BEFORE returning 202."""
    from evoblue_video_mcp.web import create_app

    service = IndexRebuildService(session_factory)  # no settings, no fallback
    app = create_app(session_factory=session_factory, index_rebuild_service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        resp = await client.post("/api/index/rebuild", json={})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SEARCH_INDEX_UNAVAILABLE"
    # Review P1-2: the pre-flight failure is VISIBLE in the index state —
    # needs_rebuild + ROOT_UNAVAILABLE, not a stale idle.
    async with session_factory() as sess:
        snapshot = await get_index_status(sess)
    assert snapshot.state == "needs_rebuild"
    assert snapshot.last_error_code == "ROOT_UNAVAILABLE"


async def test_purge_keeps_records_of_present_but_unclaimed_files(env: _Env) -> None:
    """Review P1-C: purge_missing physically deletes ONLY records whose file is
    genuinely gone from disk. A present-but-corrupted file's record is kept
    (missing, out of search) with its issue open — audit over convenience."""
    from evoblue_video_mcp.storage.report_repository import count_open_issues

    _write(env.report_dir, "gone.md", job_id="job-gone", title="真消失")
    _write(env.report_dir, "broken.md", job_id="job-broken", title="将损坏")
    await env.service.run_rebuild()
    (env.report_dir / "gone.md").unlink()
    _write(env.report_dir, "broken.md", content="broken beyond repair")

    outcome = await env.service.run_rebuild(purge=True)
    assert outcome.purged == 1  # only the vanished file
    assert outcome.removed == 1  # the corrupted one only exits search
    async with env.session_factory() as sess:
        assert await get_report_document(sess, job_id="job-gone") is None
        broken = await get_report_document(sess, job_id="job-broken")
        assert broken is not None and broken.doc_status == "missing"
        assert await count_open_issues(sess) == 1  # its parse issue stays open
    assert await _snapshot_search(env.session_factory, "将损坏") == []


async def test_pointer_file_recovers_custom_directory_after_db_deletion(
    tmp_path: Path,
) -> None:
    """Review P1-B: a WebUI-chosen CUSTOM report directory survives database
    deletion via the out-of-database pointer file (production lifespan)."""
    from evoblue_video_mcp.config import Settings
    from evoblue_video_mcp.runtime.bootstrap import create_runtime_app
    from evoblue_video_mcp.storage.rebuild import write_report_pointer

    data_dir = tmp_path / "data"
    custom = tmp_path / "custom-reports"  # deliberately OUTSIDE data dir
    custom.mkdir(parents=True)
    for i in range(2):
        _write(custom, f"custom{i}.md", job_id=f"custom-{i}", title=f"自定义{i}")
    write_report_pointer(data_dir, custom)  # what settings PUT keeps in sync

    class _NoCredentials:
        def get_secret(self, reference: str) -> str | None:
            return None

    async def _no_handlers(settings: object) -> None:
        return None

    settings = Settings(data_directory=data_dir)

    async def run_pass() -> int:
        app = create_runtime_app(
            settings,
            credential_store=_NoCredentials(),
            handler_factory=_no_handlers,
        )
        async with app.router.lifespan_context(app):
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            )
            async with client:
                history: dict[str, object] = {}
                for _ in range(500):
                    history = (await client.get("/api/history")).json()
                    if history["total"] == 2:
                        break
                    await asyncio.sleep(0.02)
                assert history["total"] == 2, history
                # Review P1-1: content reads share the rebuild's resolver —
                # after deletion the settings row is gone and the POINTER is
                # the only thing that still knows this custom directory.
                detail = (await client.get("/api/history/custom-0")).json()
                assert detail["core_summary"], detail
                whole = await client.get(
                    "/api/history/custom-0/report", params={"section": "full"}
                )
                assert whole.status_code == 200
                assert "自定义" in whole.json()["markdown"]
            return int(history["total"])

    assert await run_pass() == 2

    # Delete the database; the pointer file (outside SQLite) restores the
    # CUSTOM directory — the empty default fallback must not win.
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(data_dir / "evoblue.db") + suffix)
        if candidate.exists():
            candidate.unlink()
    assert await run_pass() == 2


async def test_stale_pointer_fails_safe_and_never_scans_fallback(tmp_path: Path) -> None:
    """Review P1-2: a pointer naming an unavailable directory is AUTHORITATIVE —
    the rebuild must fail (needs_rebuild/ROOT_UNAVAILABLE), never silently fall
    through to the fallback directory (scanning the wrong root would mark every
    real document missing) and never run the absence pass, even with purge."""
    from evoblue_video_mcp.storage.rebuild import (
        RebuildRootUnavailable,
        write_report_pointer,
    )

    env = await _Env(tmp_path, "stale").setup()
    try:
        pointer_file = tmp_path / "report-root.txt"
        write_report_pointer(tmp_path, env.report_dir)
        _write(env.report_dir, "real.md", job_id="job-real", title="真实文档")
        await env.service.run_rebuild()
        # The custom directory then vanishes (renamed / drive offline); the
        # settings row is cleared so the POINTER is the resolved candidate,
        # and a healthy fallback directory exists with a DIFFERENT report.
        env.report_dir.rename(tmp_path / "moved-away")
        async with env.session_factory() as sess:
            await save_app_settings(
                sess, setup_completed=True, now=NOW, report_directory=None
            )
        fallback_dir = tmp_path / "fallback-reports"
        fallback_dir.mkdir()
        _write(fallback_dir, "other.md", job_id="job-other", title="回退目录文档")
        service = IndexRebuildService(
            env.session_factory,
            fallback_report_root=fallback_dir,
            pointer_file=pointer_file,
        )
        with pytest.raises(RebuildRootUnavailable):
            await service.run_rebuild(purge=True)
        async with env.session_factory() as sess:
            snapshot = await get_index_status(sess)
            assert snapshot.state == "needs_rebuild"
            assert snapshot.last_error_code == "ROOT_UNAVAILABLE"
            record = await get_report_document(sess, job_id="job-real")
            # untouched: no fallback scan, no absence pass
            assert record is not None and record.doc_status == "active"
            assert await get_report_document(sess, job_id="job-other") is None
        hits = await _snapshot_search(env.session_factory, "真实文档")
        assert hits == ["job-real"]
    finally:
        await env.dispose()


async def test_scan_entry_guard_closes_toctou_window(env: _Env) -> None:
    """Review P1-2: the root is re-validated at the SCAN ENTRY, not only at the
    endpoint pre-flight — a directory that vanishes between the two checks must
    fail safe instead of running the absence pass on an effectively empty scan."""
    _write(env.report_dir, "a.md", job_id="job-a", title="视频A")
    await env.service.run_rebuild()
    real_resolve = env.service.resolve_report_root
    resolves = {"n": 0}

    async def vanishing_resolve() -> Path | None:
        resolves["n"] += 1
        root = await real_resolve()
        if resolves["n"] == 2 and root is not None:
            # Vanish between the endpoint pre-flight (resolve #1, which passed)
            # and the scan-entry re-check (resolve #2) — the TOCTOU window.
            root.rename(root.with_name(root.name + "-vanished"))
        return root

    env.service.resolve_report_root = vanishing_resolve  # type: ignore[method-assign]
    try:
        assert await env.service.start_rebuild(purge=True) is True  # pre-flight OK
        for _ in range(500):
            async with env.session_factory() as sess:
                snapshot = await get_index_status(sess)
            if snapshot.state == "needs_rebuild":
                break
            await asyncio.sleep(0.01)
        assert snapshot.state == "needs_rebuild"
        assert snapshot.last_error_code == "ROOT_UNAVAILABLE"
    finally:
        env.service.resolve_report_root = real_resolve  # type: ignore[method-assign]
    async with env.session_factory() as sess:
        record = await get_report_document(sess, job_id="job-a")
        assert record is not None and record.doc_status == "active"
    hits = await _snapshot_search(env.session_factory, "视频A")
    assert hits == ["job-a"]


async def test_settings_put_pointer_failure_keeps_database_and_pointer_consistent(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P2: pointer-first dual-write. A failed pointer write must abort
    the save (503) with the database AND the existing pointer untouched — never
    a database that adopted a directory its recovery pointer does not know."""
    import evoblue_video_mcp.web.app as web_app_module
    from evoblue_video_mcp.storage.rebuild import (
        read_report_pointer,
        write_report_pointer,
    )
    from evoblue_video_mcp.storage.repository import get_app_settings
    from evoblue_video_mcp.web import create_app

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pointer_file = data_dir / "report-root.txt"
    dir_a, dir_b = tmp_path / "reports-a", tmp_path / "reports-b"
    dir_a.mkdir()
    dir_b.mkdir()
    async with session_factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            now=1000.0,
            report_directory=str(dir_a),
            **_RUNNABLE_LLM_KWARGS,
        )
    write_report_pointer(data_dir, dir_a)

    def _broken(data_dir: Path, report_dir: Path | None) -> None:
        raise OSError("simulated pointer write failure")

    monkeypatch.setattr(web_app_module, "write_report_pointer", _broken)
    service = IndexRebuildService(session_factory, pointer_file=pointer_file)
    app = create_app(
        session_factory=session_factory,
        index_rebuild_service=service,
        report_pointer_file=pointer_file,
        credential_store=_RunnableCredentials(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        resp = await client.put(
            "/api/settings", json={"report_directory": str(dir_b)}
        )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "report pointer could not be updated"
    async with session_factory() as sess:
        current = await get_app_settings(sess)
    assert current is not None and current.report_directory == str(dir_a)
    assert read_report_pointer(data_dir) == dir_a
    # No stranded temp file from the failed atomic write.
    assert list(data_dir.glob("report-root.txt.tmp*")) == []

    # Positive control: once the pointer write succeeds, both stores agree.
    monkeypatch.undo()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        ok = await client.put("/api/settings", json={"report_directory": str(dir_b)})
    assert ok.status_code == 200
    async with session_factory() as sess:
        current = await get_app_settings(sess)
    assert current is not None and current.report_directory == str(dir_b)
    assert read_report_pointer(data_dir) == dir_b


async def test_scan_root_loss_after_collect_preserves_index(env: _Env) -> None:
    """Review P1: the drive can drop AFTER ``_collect`` enumerated the tree —
    every subsequent read then fails. Those failures must NOT be quarantined
    one-by-one (that would hand the absence pass a full wipe): the whole run
    aborts with needs_rebuild/ROOT_UNAVAILABLE and the existing index stays
    intact, even with purge."""
    from evoblue_video_mcp.storage.rebuild import RebuildRootUnavailable

    _write(env.report_dir, "a.md", job_id="job-a", title="视频A")
    await env.service.run_rebuild()
    real_collect = env.service._collect

    def collect_then_drive_drops(root: Path) -> list[str]:
        files = real_collect(root)  # enumeration succeeds…
        root.rename(root.with_name(root.name + "-offline"))  # …then the drive dies
        return files

    env.service._collect = collect_then_drive_drops  # type: ignore[method-assign]
    try:
        with pytest.raises(RebuildRootUnavailable):
            await env.service.run_rebuild(purge=True)
    finally:
        env.service._collect = real_collect  # type: ignore[method-assign]
    async with env.session_factory() as sess:
        snapshot = await get_index_status(sess)
        assert snapshot.state == "needs_rebuild"
        assert snapshot.last_error_code == "ROOT_UNAVAILABLE"
        record = await get_report_document(sess, job_id="job-a")
        # untouched: reads were never treated as single-file quarantines and
        # the absence pass never ran
        assert record is not None and record.doc_status == "active"
        verification = await verify_fts(sess)
        assert verification.consistent
    assert await _snapshot_search(env.session_factory, "视频A") == ["job-a"]


async def test_absence_pass_aborts_when_root_vanishes_after_batches(
    env: _Env,
) -> None:
    """Review P1: even when every batch read SUCCEEDS, the root is re-verified
    right before the absence pass — a directory that vanishes in that window
    must terminate the run instead of letting destructive absence decisions
    (missing/purge) execute against a root that is no longer there."""
    from evoblue_video_mcp.storage.rebuild import RebuildRootUnavailable

    _write(env.report_dir, "a.md", job_id="job-a", title="视频A")
    await env.service.run_rebuild()
    real_process = env.service._process_batch
    batches = {"n": 0}

    async def process_then_drive_drops(
        root: Path,
        rel_paths: list[str],
        claims: dict[str, object],
        scanned: list[str],
    ) -> dict[str, int]:
        result = await real_process(root, rel_paths, claims, scanned)
        batches["n"] += 1
        root.rename(root.with_name(root.name + "-offline"))  # dies after the batch
        return result

    env.service._process_batch = process_then_drive_drops  # type: ignore[method-assign]
    try:
        with pytest.raises(RebuildRootUnavailable):
            await env.service.run_rebuild(purge=True)
    finally:
        env.service._process_batch = real_process  # type: ignore[method-assign]
    assert batches["n"] == 1  # batches committed, then the gate fired
    async with env.session_factory() as sess:
        snapshot = await get_index_status(sess)
        assert snapshot.state == "needs_rebuild"
        assert snapshot.last_error_code == "ROOT_UNAVAILABLE"
        record = await get_report_document(sess, job_id="job-a")
        assert record is not None and record.doc_status == "active"
    assert await _snapshot_search(env.session_factory, "视频A") == ["job-a"]


async def test_unreadable_pointer_fails_closed_never_scans_fallback(
    env: _Env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P1: a pointer that EXISTS but is unreadable (permission change,
    AV lock, corruption) must fail closed with REPORT_POINTER_UNAVAILABLE —
    collapsing it to "no pointer" would scan the healthy fallback directory
    while a custom directory is configured, marking every real document
    missing."""
    from evoblue_video_mcp.storage.rebuild import (
        REPORT_POINTER_FILENAME,
        RebuildRootUnavailable,
        write_report_pointer,
    )

    pointer_file = tmp_path / REPORT_POINTER_FILENAME
    write_report_pointer(tmp_path, env.report_dir)
    _write(env.report_dir, "real.md", job_id="job-real", title="真实文档")
    await env.service.run_rebuild()
    # The settings row is cleared so the POINTER is the resolved candidate,
    # and a healthy fallback directory exists with a DIFFERENT report.
    async with env.session_factory() as sess:
        await save_app_settings(sess, setup_completed=True, now=NOW, report_directory=None)
    fallback_dir = tmp_path / "fallback-reports"
    fallback_dir.mkdir()
    _write(fallback_dir, "other.md", job_id="job-other", title="回退目录文档")
    service = IndexRebuildService(
        env.session_factory,
        fallback_report_root=fallback_dir,
        pointer_file=pointer_file,
    )
    # The pointer file is there but unreadable.
    original_read_text = Path.read_text

    def _denied(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == REPORT_POINTER_FILENAME:
            raise PermissionError(13, "pointer locked by another process")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _denied)
    try:
        with pytest.raises(RebuildRootUnavailable):
            await service.run_rebuild(purge=True)
    finally:
        monkeypatch.undo()
    async with env.session_factory() as sess:
        snapshot = await get_index_status(sess)
        assert snapshot.state == "needs_rebuild"
        assert snapshot.last_error_code == "REPORT_POINTER_UNAVAILABLE"
        record = await get_report_document(sess, job_id="job-real")
        assert record is not None and record.doc_status == "active"
        # the healthy fallback was NEVER scanned
        assert await get_report_document(sess, job_id="job-other") is None
        verification = await verify_fts(sess)
        assert verification.consistent
    assert await _snapshot_search(env.session_factory, "真实文档") == ["job-real"]


async def test_startup_parks_root_unavailable_for_empty_database(env: _Env) -> None:
    """Review P2: a fresh/deleted database (zero documents) with an unusable
    root must not sit at a lying ``idle`` — startup reconciliation parks
    needs_rebuild/ROOT_UNAVAILABLE so /api/index/status explains the empty
    history."""
    async with env.session_factory() as sess:
        await save_app_settings(sess, setup_completed=True, now=NOW, report_directory=None)
    await env.service.reconcile_on_startup()
    async with env.session_factory() as sess:
        snapshot = await get_index_status(sess)
    assert snapshot.state == "needs_rebuild"
    assert snapshot.last_error_code == "ROOT_UNAVAILABLE"


async def test_startup_preserves_specific_root_code_over_generic_failure(
    env: _Env,
    tmp_path: Path,
) -> None:
    """Review P2: when the automatic rebuild fails on an unusable root, the
    startup path must keep the SPECIFIC code — the outer catch must not
    overwrite ROOT_UNAVAILABLE with the generic REBUILD_FAILED."""
    _write(env.report_dir, "a.md", job_id="job-a", title="视频A")
    await env.service.run_rebuild()
    env.report_dir.rename(tmp_path / "offline-drive")  # root dies before restart
    async with env.session_factory() as sess:
        await set_index_state(sess, state="needs_rebuild", now=NOW, error_code=None)
        await sess.commit()
    await env.service.reconcile_on_startup()  # parks needs_rebuild → run fails
    async with env.session_factory() as sess:
        snapshot = await get_index_status(sess)
    assert snapshot.state == "needs_rebuild"
    assert snapshot.last_error_code == "ROOT_UNAVAILABLE"  # not REBUILD_FAILED


async def test_issue_detail_never_leaks_absolute_path(
    env: _Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P2: ``str(OSError)`` carries the report's absolute path; the
    persisted detail (and therefore /api/index/issues) must carry only the
    exception TYPE — the raw message stays in the local log."""
    from evoblue_video_mcp.web import create_app

    _write(env.report_dir, "good.md", job_id="job-g", title="好文档")
    _write(env.report_dir, "locked.md", job_id="job-l", title="锁定文档")
    original_read_bytes = Path.read_bytes

    def _denied(self: Path, *args: object, **kwargs: object) -> bytes:
        if self.name == "locked.md":
            raise PermissionError(13, f"denied: {self}")
        return original_read_bytes(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_bytes", _denied)
    outcome = await env.service.run_rebuild()
    monkeypatch.undo()
    assert outcome.indexed == 1 and outcome.quarantined == 1
    async with env.session_factory() as sess:
        issues = await list_open_issues(sess)
    detail = next(i.detail for i in issues if i.relative_path == "locked.md")
    assert detail is not None
    assert "PermissionError" in detail
    assert str(env.report_dir) not in detail
    # The HTTP surface must be equally clean.
    app = create_app(session_factory=env.session_factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        resp = await client.get("/api/index/issues")
    assert resp.status_code == 200
    assert str(env.report_dir) not in resp.text
    assert "denied" not in resp.text


async def test_concurrent_settings_puts_serialize_to_consistent_state(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Review P1: two concurrent PUTs must not interleave into pointer=B /
    database=A. The in-process lock serializes read-pointer → write-pointer →
    commit-database, so both 200s end with the SAME value in both stores."""
    from evoblue_video_mcp.storage.rebuild import (
        read_report_pointer,
        write_report_pointer,
    )
    from evoblue_video_mcp.storage.repository import get_app_settings
    from evoblue_video_mcp.web import create_app

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pointer_file = data_dir / "report-root.txt"
    dir_a, dir_b, dir_c = tmp_path / "ra", tmp_path / "rb", tmp_path / "rc"
    for directory in (dir_a, dir_b, dir_c):
        directory.mkdir()
    async with session_factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            now=1000.0,
            report_directory=str(dir_a),
            **_RUNNABLE_LLM_KWARGS,
        )
    write_report_pointer(data_dir, dir_a)
    app = create_app(
        session_factory=session_factory,
        report_pointer_file=pointer_file,
        credential_store=_RunnableCredentials(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        first, second = await asyncio.gather(
            client.put("/api/settings", json={"report_directory": str(dir_b)}),
            client.put("/api/settings", json={"report_directory": str(dir_c)}),
        )
    assert first.status_code == 200 and second.status_code == 200
    async with session_factory() as sess:
        current = await get_app_settings(sess)
    assert current is not None
    assert current.report_directory in {str(dir_b), str(dir_c)}
    # Both stores agree on the SAME value — whichever PUT committed last won
    # COMPLETELY (pointer and database together).
    assert read_report_pointer(data_dir) == Path(current.report_directory)


async def test_settings_put_db_failure_compensates_pointer(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P1: when the database commit fails AFTER the pointer was
    written, the endpoint restores the previous pointer and answers 503 — a
    failed save never leaves a new pointer over an old database."""
    import evoblue_video_mcp.web.app as web_app_module
    from evoblue_video_mcp.storage.rebuild import (
        read_report_pointer,
        write_report_pointer,
    )
    from evoblue_video_mcp.storage.repository import get_app_settings
    from evoblue_video_mcp.web import create_app

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pointer_file = data_dir / "report-root.txt"
    dir_a, dir_b = tmp_path / "reports-a", tmp_path / "reports-b"
    dir_a.mkdir()
    dir_b.mkdir()
    async with session_factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            now=1000.0,
            report_directory=str(dir_a),
            **_RUNNABLE_LLM_KWARGS,
        )
    write_report_pointer(data_dir, dir_a)

    def _db_boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(web_app_module, "save_app_settings", _db_boom)
    app = create_app(
        session_factory=session_factory,
        report_pointer_file=pointer_file,
        credential_store=_RunnableCredentials(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        resp = await client.put(
            "/api/settings", json={"report_directory": str(dir_b)}
        )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "settings could not be saved"
    async with session_factory() as sess:
        current = await get_app_settings(sess)
    assert current is not None and current.report_directory == str(dir_a)
    # Compensation: the pointer was put back, no stranded temp file.
    assert read_report_pointer(data_dir) == dir_a
    assert list(data_dir.glob("report-root.txt.tmp*")) == []

    # Positive control: the save succeeds once the fault is gone.
    monkeypatch.undo()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        ok = await client.put("/api/settings", json={"report_directory": str(dir_b)})
    assert ok.status_code == 200
    async with session_factory() as sess:
        current = await get_app_settings(sess)
    assert current is not None and current.report_directory == str(dir_b)
    assert read_report_pointer(data_dir) == dir_b


async def test_settings_put_clear_pointer_failure_blocks_db_commit(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P1: clearing the pointer goes through the same atomic path, and
    a failed clear must refuse the database commit — a silently cleared
    pointer would let the old directory revive after a database deletion."""
    import evoblue_video_mcp.web.app as web_app_module
    from evoblue_video_mcp.storage.rebuild import (
        read_report_pointer,
        write_report_pointer,
    )
    from evoblue_video_mcp.storage.repository import get_app_settings
    from evoblue_video_mcp.web import create_app

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pointer_file = data_dir / "report-root.txt"
    dir_a = tmp_path / "reports-a"
    dir_a.mkdir()
    async with session_factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            now=1000.0,
            report_directory=str(dir_a),
            **_RUNNABLE_LLM_KWARGS,
        )
    write_report_pointer(data_dir, dir_a)

    def _broken(data_dir: Path, report_dir: Path | None) -> None:
        raise OSError("simulated pointer clear failure")

    monkeypatch.setattr(web_app_module, "write_report_pointer", _broken)
    app = create_app(
        session_factory=session_factory,
        report_pointer_file=pointer_file,
        credential_store=_RunnableCredentials(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        resp = await client.put("/api/settings", json={"report_directory": None})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "report pointer could not be updated"
    async with session_factory() as sess:
        current = await get_app_settings(sess)
    # The database did NOT move to NULL while the old pointer survived.
    assert current is not None and current.report_directory == str(dir_a)
    assert read_report_pointer(data_dir) == dir_a

    # Positive control: a successful clear writes the tombstone and NULLs the db.
    monkeypatch.undo()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        ok = await client.put("/api/settings", json={"report_directory": None})
    assert ok.status_code == 200
    async with session_factory() as sess:
        current = await get_app_settings(sess)
    assert current is not None and current.report_directory is None
    assert read_report_pointer(data_dir) is None  # tombstone reads as cleared
    assert (data_dir / "report-root.txt").exists()  # atomic tombstone, not unlink


async def test_batch_reads_hold_no_write_lock(env: _Env, monkeypatch) -> None:
    """Review P1: the rescan's file reads and parses happen OUTSIDE any
    transaction — the write lock is never held across disk I/O. While the
    batch is still READING files, a concurrent writer can commit immediately;
    under the old long-held IMMEDIATE batch transaction this write would
    stall on the busy timeout and fail."""
    import sqlite3

    _write(env.report_dir, "a.md", job_id="job-a", title="视频A")
    _write(env.report_dir, "b.md", job_id="job-b", title="视频B")
    await env.service.run_rebuild()

    fired = {"n": 0}
    original_read_bytes = Path.read_bytes

    def read_then_commit_elsewhere(self: Path, *args: object, **kwargs: object) -> bytes:
        data = original_read_bytes(self, *args, **kwargs)  # type: ignore[arg-type]
        if fired["n"] == 0 and self.suffix == ".md":
            fired["n"] += 1
            # A DIFFERENT connection commits while the rescan is mid-batch.
            conn = sqlite3.connect(str(env.db_path), timeout=2.0)
            try:
                conn.execute(
                    "UPDATE index_status SET started_at = COALESCE(started_at, 0) + 1 "
                    "WHERE id = 1"
                )
                conn.commit()
            finally:
                conn.close()
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_commit_elsewhere)
    try:
        outcome = await env.service.run_rebuild()
    finally:
        monkeypatch.undo()
    assert fired["n"] == 1  # the concurrent commit ran DURING the batch reads
    assert outcome.unchanged == 2 and outcome.consistent


async def test_start_rebuild_pointer_error_parks_status(
    env: _Env, tmp_path: Path, monkeypatch
) -> None:
    """Review P2: a resolver failure at the endpoint pre-flight (unreadable
    recovery pointer) must park needs_rebuild with the SPECIFIC code before
    the 503 — same discipline as the unusable-root path — and release the
    scheduled flag so a later trigger is not wrongly answered 409."""
    from evoblue_video_mcp.storage.rebuild import (
        ReportPointerUnavailable,
        write_report_pointer,
    )
    from evoblue_video_mcp.web import create_app

    pointer_file = tmp_path / "report-root.txt"
    write_report_pointer(tmp_path, env.report_dir)
    service = IndexRebuildService(
        env.session_factory,
        pointer_file=pointer_file,
    )

    async def raising_resolve() -> Path | None:
        raise ReportPointerUnavailable("recovery pointer exists but cannot be read")

    monkeypatch.setattr(service, "resolve_report_root", raising_resolve)  # type: ignore[method-assign]
    app = create_app(session_factory=env.session_factory, index_rebuild_service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        first = await client.post("/api/index/rebuild", json={})
        assert first.status_code == 503
        assert first.json()["error"]["code"] == "SEARCH_INDEX_UNAVAILABLE"
        # The scheduled flag was released: the second trigger re-enters the
        # pre-flight (503 again), it is NOT a spurious 409.
        second = await client.post("/api/index/rebuild", json={})
        assert second.status_code == 503
    async with env.session_factory() as sess:
        snapshot = await get_index_status(sess)
    assert snapshot.state == "needs_rebuild"
    assert snapshot.last_error_code == "REPORT_POINTER_UNAVAILABLE"


async def test_settings_put_cancellation_compensates_pointer(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Review P2: a PUT cancelled BETWEEN the pointer replace and the database
    commit must restore the previous pointer (compensation covers
    CANCELLATION, not just Exception) — both cancel barriers: while the save
    is in flight, and a cancellation raised mid-commit."""
    import asyncio

    import evoblue_video_mcp.web.app as web_app_module
    from evoblue_video_mcp.storage.rebuild import (
        read_report_pointer,
        write_report_pointer,
    )
    from evoblue_video_mcp.storage.repository import get_app_settings
    from evoblue_video_mcp.web import create_app

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pointer_file = data_dir / "report-root.txt"
    dir_a, dir_b = tmp_path / "reports-a", tmp_path / "reports-b"
    dir_a.mkdir()
    dir_b.mkdir()
    async with session_factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            now=1000.0,
            report_directory=str(dir_a),
            **_RUNNABLE_LLM_KWARGS,
        )
    write_report_pointer(data_dir, dir_a)
    app = create_app(
        session_factory=session_factory,
        report_pointer_file=pointer_file,
        credential_store=_RunnableCredentials(),
    )

    async def assert_compensated() -> None:
        async with session_factory() as sess:
            current = await get_app_settings(sess)
        assert current is not None and current.report_directory == str(dir_a)
        assert read_report_pointer(data_dir) == dir_a
        assert list(data_dir.glob("report-root.txt.tmp*")) == []

    # Barrier 1: cancellation lands while the save is in flight (after the
    # pointer was already replaced).
    async def hanging_save(*args: object, **kwargs: object) -> None:
        await asyncio.Event().wait()  # cancellable, never completes

    monkeypatch.setattr(web_app_module, "save_app_settings", hanging_save)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        task = asyncio.create_task(
            client.put("/api/settings", json={"report_directory": str(dir_b)})
        )
        await asyncio.sleep(0.1)  # let the PUT write the pointer, then park
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await assert_compensated()
    monkeypatch.undo()

    # Barrier 2: a cancellation raised in the middle of the commit itself.
    async def cancelled_mid_commit(*args: object, **kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(web_app_module, "save_app_settings", cancelled_mid_commit)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.put("/api/settings", json={"report_directory": str(dir_b)})
    await assert_compensated()
    monkeypatch.undo()


async def test_settings_put_post_commit_failure_never_compensates(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Review P1: once the database commit SUCCEEDED, a later cancellation or
    failure (post-commit reconcile, response encoding) must NOT restore the
    old pointer — that would push the stores apart (SQLite=new directory,
    pointer=old). With ``commit=False`` the commit is the LAST statement of
    the endpoint's transaction, so compensation only ever covers the
    pre-commit window; this cancel lands in the post-commit reconcile and the
    two stores must end up CONSISTENT on the new value. (The old
    post-commit re-read inside ``save_app_settings`` — whose failure looked
    like a failed save — no longer exists on this path.)"""
    import asyncio

    from evoblue_video_mcp.storage.rebuild import (
        read_report_pointer,
        write_report_pointer,
    )
    from evoblue_video_mcp.storage.repository import get_app_settings
    from evoblue_video_mcp.web import create_app

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pointer_file = data_dir / "report-root.txt"
    dir_a, dir_b = tmp_path / "reports-a", tmp_path / "reports-b"
    dir_a.mkdir()
    dir_b.mkdir()
    async with session_factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            now=1000.0,
            report_directory=str(dir_a),
            **_RUNNABLE_LLM_KWARGS,
        )
    write_report_pointer(data_dir, dir_a)

    entered = asyncio.Event()
    release = asyncio.Event()

    class _HangingReconcile:
        async def reconcile_waiting(
            self, *, retry_failed_loads: bool = False
        ) -> list[str]:
            entered.set()  # the commit has SUCCEEDED; we are post-commit now
            await release.wait()
            return []

    app = create_app(
        session_factory=session_factory,
        report_pointer_file=pointer_file,
        model_service=_HangingReconcile(),  # type: ignore[arg-type]
        credential_store=_RunnableCredentials(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        task = asyncio.create_task(
            client.put(
                "/api/settings",
                json={
                    "report_directory": str(dir_b),
                    "whisper_cpp_executable": "C:/x/whisper-cli.exe",
                },
            )
        )
        # bounded: a regression that rejects the PUT must FAIL here, not hang
        await asyncio.wait_for(entered.wait(), timeout=10)
        task.cancel()  # cancel AFTER the commit, during the reconcile
        with pytest.raises(asyncio.CancelledError):
            await task

    # Both stores hold the NEW value — consistent; compensation never fired.
    async with session_factory() as sess:
        current = await get_app_settings(sess)
    assert current is not None and current.report_directory == str(dir_b)
    assert read_report_pointer(data_dir) == dir_b
    assert list(data_dir.glob("report-root.txt.tmp*")) == []
