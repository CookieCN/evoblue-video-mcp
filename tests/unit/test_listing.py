"""P4-004: list_analysis_jobs page helpers (MCP_TOOLS §4, ADR 0004).

R9 (review round 4): the default view is the unified one-snapshot endpoint;
R4b: explicit statuses are single-source pushed-down queries. These tests
exercise the production entry points directly — ``single_source_page``,
``completed_page``, and ``unified_page`` — with wire-complete bodies
(mirroring the engine REST schemas exactly) plus the malformed-body
counterexamples from review rounds 5-7. Malformed fixtures always start
from an otherwise-valid baseline and vary exactly the field under test.
"""

import copy

import pytest

from evoblue_video_mcp.mcp.listing import (
    EngineListFormatError,
    completed_page,
    single_source_page,
    unified_page,
)


def _job_row(job_id: str, status: str = "summarizing_chunks", **extra: object) -> dict:
    row: dict = {
        "job_id": job_id,
        "status": status,
        "progress": 40,
        "created_at": 1000.0,
    }
    row.update(extra)
    return row


def _history_row(job_id: str, **extra: object) -> dict:
    """A wire-complete HistoryItem (mirrors web.schemas.HistoryItem)."""
    row: dict = {
        "job_id": job_id,
        "analysis_id": job_id,
        "title": f"title-{job_id}",
        "platform": "youtube",
        "author": "a",
        "video_id": job_id,
        "source_url": f"https://youtu.be/{job_id}",
        "published_at": None,
        "analyzed_at": 100.0,
        "language": "zh",
        "summary_mode": "auto",
        "asr_provider": "platform",
        "asr_model": "subtitles",
        "asr_model_version": "v1",
        "tags": [],
        "summary_preview": "…",
        "file_path": f"{job_id}.md",
        "content_hash": "0" * 64,
        "doc_status": "active",
        "indexed_at": 100.0,
    }
    row.update(extra)
    return row


def _unified_job_row(job_id: str, status: str = "summarizing_chunks", **extra: object) -> dict:
    row: dict = {"kind": "job", "job_id": job_id, "status": status, "progress": 40}
    row.update(extra)
    return row


def _unified_history_row(job_id: str, **extra: object) -> dict:
    row: dict = {
        "kind": "history",
        "job_id": job_id,
        "status": "completed",
        "progress": 100,
        "title": f"title-{job_id}",
        "platform": "youtube",
    }
    row.update(extra)
    return row


_HISTORY_BODY = {
    "items": [
        _history_row("h1", title="选品方法论"),
        _history_row("h2", title="TikTok 起号", platform="bilibili"),
    ],
    "total": 2,
    "limit": 20,
    "offset": 0,
}
_UNIFIED_BODY = {
    "items": [
        _unified_job_row("j1"),
        _unified_job_row("j2", status="queued", progress=0),
        _unified_history_row("h1", title="选品方法论"),
        _unified_history_row("h2", title="TikTok 起号", platform="bilibili"),
    ],
    "total": 4,
    "jobs_total": 2,
    "history_total": 2,
    "limit": 20,
    "offset": 0,
}


def _unified(body: dict | None = None, **kwargs: object) -> object:
    params: dict[str, object] = {"limit": 20, "offset": 0}
    params.update(kwargs)
    return unified_page(dict(body if body is not None else _UNIFIED_BODY), **params)  # type: ignore[arg-type]


def test_unified_page_concatenates_segments_with_metadata() -> None:
    output = _unified()
    items = output.items
    assert [item.job_id for item in items] == ["j1", "j2", "h1", "h2"]
    assert items[0].title is None and items[0].platform is None  # job rows: no metadata yet
    assert items[2].title == "选品方法论" and items[2].platform == "youtube"
    assert all(item.status == "completed" and item.progress == 100 for item in items[2:])
    assert output.total == 4
    assert output.ok is True


def test_unified_page_passes_identity_and_failure_attribution() -> None:
    """F3/F4: job rows carry title/platform/url once metadata lands, and
    terminal rows keep error_code for failure attribution."""
    body = {
        "items": [
            _unified_job_row(
                "j1",
                status="waiting_for_model",
                progress=0,
                title="等待中的任务",
                platform="bilibili",
                url="https://www.bilibili.com/video/BV1x",
            ),
            _unified_job_row(
                "f1",
                status="failed",
                progress=0,
                error_code="LLM_AUTH_FAILED",
                url="https://youtu.be/f1",
            ),
        ],
        "total": 2,
        "jobs_total": 2,
        "history_total": 0,
        "limit": 20,
        "offset": 0,
    }
    output = _unified(body)
    assert output.items[0].title == "等待中的任务"
    assert output.items[0].url == "https://www.bilibili.com/video/BV1x"
    assert output.items[1].error_code == "LLM_AUTH_FAILED"
    assert output.total == 2


def test_unified_page_reports_request_window_verbatim() -> None:
    # The engine already sliced the window; the page reports the request's
    # limit/offset verbatim and the snapshot total on every page.
    body = copy.deepcopy(_UNIFIED_BODY)
    body["limit"] = 2
    body["offset"] = 1
    body["items"] = _UNIFIED_BODY["items"][1:3]  # the exact [1, 3) window
    output = unified_page(body, limit=2, offset=1)
    assert [item.job_id for item in output.items] == ["j2", "h1"]
    assert output.offset == 1 and output.limit == 2
    assert output.total == 4


def test_unified_page_total_is_server_side_exact() -> None:
    # A tail page: the 5011th row of 5011 — total far beyond the fetched page.
    body = {
        "items": [_unified_history_row("h5000")],
        "total": 5011,
        "jobs_total": 4,
        "history_total": 5007,
        "limit": 20,
        "offset": 5010,
    }
    output = unified_page(body, limit=20, offset=5010)
    assert output.total == 5011


def test_status_filter_gates_history_source() -> None:
    # F4 (feedback #15): an explicit terminal filter is a single-source query
    # over /api/jobs — items pass through as-is (no "active-only" drop) and
    # total is the server-side exact count. A tail page keeps that meaning:
    # 7 failures, the last one fetched.
    failed_jobs = {
        "items": [
            _job_row(
                "f1",
                status="failed",
                progress=0,
                error_code="ASR_MODEL_MISSING",
            )
        ],
        "total": 7,
        "limit": 20,
        "offset": 6,
    }
    failed = single_source_page(failed_jobs, status="failed", limit=20, offset=6)
    assert [item.job_id for item in failed.items] == ["f1"]
    assert failed.items[0].error_code == "ASR_MODEL_MISSING"
    assert failed.total == 7  # server total, beyond the fetched page

    cancelled_jobs = {
        "items": [_job_row("c1", status="cancelled", progress=0)],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }
    cancelled = single_source_page(cancelled_jobs, status="cancelled", limit=20, offset=0)
    assert [item.job_id for item in cancelled.items] == ["c1"]

    queued_jobs = {
        "items": [_job_row("j2", status="queued", progress=0)],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }
    queued = single_source_page(queued_jobs, status="queued", limit=20, offset=0)
    assert [item.job_id for item in queued.items] == ["j2"]

    # completed rides the history source instead
    completed = completed_page(dict(_HISTORY_BODY), limit=20, offset=0)
    assert [item.job_id for item in completed.items] == ["h1", "h2"]
    assert completed.total == 2


def test_explicit_status_passes_server_filtered_page_through() -> None:
    """R4 (review): platform/query filtering for an explicit status runs on
    the ENGINE — the returned body IS the filtered page, passed through
    untouched with the server's exact total."""
    failed_jobs = {
        "items": [_job_row("f2", status="failed", progress=0, platform="bilibili")],
        "total": 9,
        "limit": 20,
        "offset": 8,
    }
    output = single_source_page(failed_jobs, status="failed", limit=20, offset=8)
    assert [item.job_id for item in output.items] == ["f2"]
    assert output.items[0].platform == "bilibili"
    assert output.total == 9


def test_single_source_rejects_status_mismatch_instead_of_dropping() -> None:
    """R14: an item whose status does not match the requested filter is a
    contract violation — the old silent drop masked engine bugs."""
    body = {
        "items": [_job_row("x", status="queued", progress=0)],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }
    with pytest.raises(EngineListFormatError):
        single_source_page(body, status="failed", limit=20, offset=0)


def test_page_rejects_malformed_top_level_shape() -> None:
    """R13 (review round 5): a body missing items/total must raise, never
    degrade to a successful empty page that silently hides everything."""
    for call in (
        lambda: unified_page({"unexpected": "shape"}, limit=20, offset=0),
        lambda: completed_page({"unexpected": "shape"}, limit=20, offset=0),
        lambda: single_source_page(
            {"unexpected": "shape"}, status="failed", limit=20, offset=0
        ),
        lambda: unified_page({"items": []}, limit=20, offset=0),  # total missing
        lambda: unified_page({"items": [], "total": -1}, limit=20, offset=0),
        lambda: unified_page({"items": {}, "total": 0}, limit=20, offset=0),  # not a list
    ):
        with pytest.raises(EngineListFormatError):
            call()


def test_window_echo_rejects_bool() -> None:
    """R16 (review round 7): Python's ``True == 1`` would let a boolean
    window echo slip through plain equality — reject bools explicitly."""
    body = copy.deepcopy(_UNIFIED_BODY)
    body["limit"] = True
    with pytest.raises(EngineListFormatError):
        unified_page(body, limit=1, offset=0)
    body = copy.deepcopy(_UNIFIED_BODY)
    body["offset"] = False
    with pytest.raises(EngineListFormatError):
        unified_page(body, limit=20, offset=0)


def test_wire_types_are_never_coerced() -> None:
    """R16 (review round 7): wrong-typed fields are violations, never data
    to fix — numeric job_id, boolean progress, list title, and missing
    REST-required fields all degrade instead of being coerced away."""

    def jobs_body(row: dict) -> dict:
        return {"items": [row], "total": 1, "limit": 20, "offset": 0}

    baseline = _job_row("x", status="failed", progress=0)
    for mutation in (
        {"job_id": 123},           # numeric job_id
        {"progress": True},        # boolean progress (True == 1)
        {"title": ["bad"]},        # list title
        {"progress": "0"},         # string progress
    ):
        row = dict(baseline)
        row.update(mutation)
        with pytest.raises(EngineListFormatError):
            single_source_page(jobs_body(row), status="failed", limit=20, offset=0)

    missing_progress = {k: v for k, v in baseline.items() if k != "progress"}
    with pytest.raises(EngineListFormatError):
        single_source_page(jobs_body(missing_progress), status="failed", limit=20, offset=0)
    missing_created = {k: v for k, v in baseline.items() if k != "created_at"}
    with pytest.raises(EngineListFormatError):
        single_source_page(jobs_body(missing_created), status="failed", limit=20, offset=0)

    # history source: numeric job_id on an otherwise-complete HistoryItem
    bad_history = dict(_HISTORY_BODY)
    numeric_row = _history_row("h1")
    numeric_row["job_id"] = 123
    bad_history["items"] = [numeric_row]
    with pytest.raises(EngineListFormatError):
        completed_page(bad_history, limit=20, offset=0)
    # history source: a required HistoryItem field missing
    incomplete = dict(_HISTORY_BODY)
    row = _history_row("h1")
    del row["analyzed_at"]
    incomplete["items"] = [row]
    with pytest.raises(EngineListFormatError):
        completed_page(incomplete, limit=20, offset=0)

    # unified job row missing the REST-required progress
    missing_unified_progress = dict(_UNIFIED_BODY)
    row = _unified_job_row("j1")
    del row["progress"]
    missing_unified_progress["items"] = [row]
    missing_unified_progress["total"] = 1
    missing_unified_progress["jobs_total"] = 1
    missing_unified_progress["history_total"] = 0
    with pytest.raises(EngineListFormatError):
        _unified(missing_unified_progress)


def test_page_rejects_malformed_items() -> None:
    """R13: per-item validation failures surface as EngineListFormatError,
    not KeyError/ValidationError — each fixture below starts from an
    otherwise-valid baseline and varies exactly one field (review round 7:
    fixtures must exercise the ITEM path, not the window/segment checks)."""

    def unified_body(rows: list[dict]) -> dict:
        return {
            "items": rows,
            "total": len(rows),
            "jobs_total": sum(
                1 for r in rows if isinstance(r, dict) and r.get("kind") == "job"
            ),
            "history_total": sum(
                1 for r in rows if isinstance(r, dict) and r.get("kind") == "history"
            ),
            "limit": 20,
            "offset": 0,
        }

    with pytest.raises(EngineListFormatError):
        # no kind
        row = {"job_id": "x", "status": "failed", "progress": 0}
        unified_page(unified_body([row]), limit=20, offset=0)
    with pytest.raises(EngineListFormatError):
        unified_page(unified_body([_unified_job_row("x", kind="video")]), limit=20, offset=0)
    with pytest.raises(EngineListFormatError):
        # status missing
        row = {"kind": "job", "job_id": "x", "progress": 0}
        unified_page(unified_body([row]), limit=20, offset=0)
    with pytest.raises(EngineListFormatError):
        unified_page(unified_body([_unified_job_row("x", status="bogus")]), limit=20, offset=0)
    with pytest.raises(EngineListFormatError):
        # history missing job_id
        row = {"kind": "history", "status": "completed", "progress": 100}
        unified_page(unified_body([row]), limit=20, offset=0)
    with pytest.raises(EngineListFormatError):
        # non-object row
        unified_page(unified_body(["oops"]), limit=20, offset=0)
    with pytest.raises(EngineListFormatError):
        completed_page(
            {"items": [42], "total": 1, "limit": 20, "offset": 0},
            limit=20,
            offset=0,
        )
    with pytest.raises(EngineListFormatError):
        single_source_page(
            {"items": [None], "total": 1, "limit": 20, "offset": 0},
            status="failed",
            limit=20,
            offset=0,
        )


def _consistent_unified(**overrides: object) -> dict:
    body: dict = {
        "items": [
            _unified_job_row("j1", status="failed", progress=0),
            _unified_history_row("h1"),
        ],
        "total": 2,
        "jobs_total": 1,
        "history_total": 1,
        "limit": 5,
        "offset": 0,
    }
    body.update(overrides)
    return body


def test_unified_invariants_segment_counts_and_window() -> None:
    """R15 (review round 6): the unified response carries the FULL REST
    contract — jobs_total/history_total required, total must be their sum,
    and the response window must echo the request."""
    assert unified_page(_consistent_unified(), limit=5, offset=0).total == 2

    missing_counts = copy.deepcopy(_consistent_unified())
    del missing_counts["jobs_total"]
    with pytest.raises(EngineListFormatError):
        unified_page(missing_counts, limit=5, offset=0)

    mismatched = _consistent_unified(total=3)  # 3 != 1 + 1
    with pytest.raises(EngineListFormatError):
        unified_page(mismatched, limit=5, offset=0)

    wrong_window = _consistent_unified(limit=4)  # engine echoed the wrong limit
    with pytest.raises(EngineListFormatError):
        unified_page(wrong_window, limit=5, offset=0)
    wrong_offset = _consistent_unified(offset=1)
    with pytest.raises(EngineListFormatError):
        unified_page(wrong_offset, limit=5, offset=0)


def test_unified_invariants_page_capacity_matches_window() -> None:
    """R15: the page's item count must equal min(limit, total - offset) — an
    empty FIRST page with a positive total still silently hides everything."""
    empty_first = _consistent_unified()
    empty_first["items"] = []
    empty_first["total"] = 7
    empty_first["jobs_total"] = 4
    empty_first["history_total"] = 3
    with pytest.raises(EngineListFormatError):
        unified_page(empty_first, limit=5, offset=0)

    over_capacity = _consistent_unified()  # 2 items but total claims 1
    over_capacity["total"] = 1
    over_capacity["jobs_total"] = 1
    over_capacity["history_total"] = 0
    with pytest.raises(EngineListFormatError):
        unified_page(over_capacity, limit=5, offset=0)


def test_unified_invariants_segment_status_ownership() -> None:
    """R15: the job segment is non-completed (a completed job row breaks the
    disjoint two-segment semantics); a history item declaring a non-completed
    status must be rejected, not silently rewritten to completed."""
    completed_job = _consistent_unified()
    completed_job["items"] = [_unified_job_row("j1", status="completed", progress=0)]
    completed_job["total"] = 1
    completed_job["jobs_total"] = 1
    completed_job["history_total"] = 0
    with pytest.raises(EngineListFormatError):
        unified_page(completed_job, limit=5, offset=0)

    failed_history = _consistent_unified()
    failed_history["items"] = [_unified_history_row("h1", status="failed")]
    failed_history["total"] = 1
    failed_history["jobs_total"] = 0
    failed_history["history_total"] = 1
    with pytest.raises(EngineListFormatError):
        unified_page(failed_history, limit=5, offset=0)


def test_unified_invariants_row_segment_position() -> None:
    """R17 (review round 7): each row's kind must match its GLOBAL position —
    global index < jobs_total is a job row, beyond it a history row. Totals
    being correct does not prove the slice is; a reversed cross-segment page
    would yield wrong pages, omissions, or cross-page duplicates."""
    reversed_page = {
        "items": [
            _unified_history_row("h1"),
            _unified_job_row("j1", status="failed", progress=0),
        ],
        "total": 2,
        "jobs_total": 1,
        "history_total": 1,
        "limit": 2,
        "offset": 0,
    }
    with pytest.raises(EngineListFormatError):
        unified_page(reversed_page, limit=2, offset=0)

    # positive controls: correct orderings all pass
    cross = {
        "items": [
            _unified_job_row("j1", status="failed", progress=0),
            _unified_history_row("h1"),
        ],
        "total": 2,
        "jobs_total": 1,
        "history_total": 1,
        "limit": 2,
        "offset": 0,
    }
    assert [i.job_id for i in unified_page(cross, limit=2, offset=0).items] == ["j1", "h1"]

    history_only = {
        "items": [_unified_history_row("h1")],
        "total": 2,
        "jobs_total": 1,
        "history_total": 1,
        "limit": 1,
        "offset": 1,
    }
    assert [i.job_id for i in unified_page(history_only, limit=1, offset=1).items] == ["h1"]

    # a job row sitting in history-only territory (offset >= jobs_total)
    wrong_territory = {
        "items": [_unified_job_row("j1", status="failed", progress=0)],
        "total": 2,
        "jobs_total": 1,
        "history_total": 1,
        "limit": 1,
        "offset": 1,
    }
    with pytest.raises(EngineListFormatError):
        unified_page(wrong_territory, limit=1, offset=1)
