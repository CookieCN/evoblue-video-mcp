"""P4-004: two-source merge for list_analysis_jobs (MCP_TOOLS §4)."""

from evoblue_video_mcp.mcp.listing import merge_job_pages

_JOBS_BODY = {
    "items": [
        {"job_id": "j1", "status": "summarizing_chunks", "progress": 40},
        {"job_id": "j2", "status": "queued", "progress": 0},
    ],
    "total": 2,
}
_HISTORY_BODY = {
    "items": [
        {"job_id": "h1", "title": "选品方法论", "platform": "youtube"},
        {"job_id": "h2", "title": "TikTok 起号", "platform": "bilibili"},
    ],
    "total": 2,
}


def _merge(**kwargs: object) -> object:
    params: dict[str, object] = {
        "limit": 20,
        "offset": 0,
        "status": None,
        "platform": None,
        "query": None,
    }
    params.update(kwargs)
    return merge_job_pages(
        dict(_JOBS_BODY), dict(_HISTORY_BODY), **params  # type: ignore[arg-type]
    )


def test_active_first_then_history_with_metadata() -> None:
    output = _merge()
    items = output.items  # type: ignore[attr-defined]
    assert [item.job_id for item in items] == ["j1", "j2", "h1", "h2"]
    assert items[0].title is None and items[0].platform is None  # active: no metadata
    assert items[2].title == "选品方法论" and items[2].platform == "youtube"
    assert all(item.status == "completed" for item in items[2:])
    assert items[2].progress == 100


def test_dedup_favors_active_entry() -> None:
    jobs = {
        "items": [{"job_id": "j1", "status": "indexing", "progress": 90}],
        "total": 1,
    }
    history = {
        "items": [{"job_id": "j1", "title": "双源任务", "platform": "youtube"}],
        "total": 1,
    }
    output = merge_job_pages(
        jobs, history, limit=20, offset=0, status=None, platform=None, query=None
    )
    assert [item.job_id for item in output.items] == ["j1"]
    assert output.items[0].status == "indexing"  # active wins
    assert output.total == 1  # deduped, not double counted


def test_platform_and_query_filter_only_history() -> None:
    output = _merge(platform="YouTube")  # case-insensitive
    assert [item.job_id for item in output.items] == ["j1", "j2", "h1"]
    output = _merge(query="tiktok")
    assert [item.job_id for item in output.items] == ["j1", "j2", "h2"]


def test_status_filter_gates_history_source() -> None:
    # status filtering is pushed down to /api/jobs server-side; the merge
    # trusts that and never lists terminal jobs as active entries.
    failed_jobs = {"items": [{"job_id": "f1", "status": "failed", "progress": 0}], "total": 1}
    failed = merge_job_pages(
        failed_jobs,
        dict(_HISTORY_BODY),
        limit=20,
        offset=0,
        status="failed",
        platform=None,
        query=None,
    )
    assert [item.job_id for item in failed.items] == []
    # Server already filtered to completed-only ⇒ empty active page.
    completed = merge_job_pages(
        {"items": [], "total": 0},
        dict(_HISTORY_BODY),
        limit=20,
        offset=0,
        status="completed",
        platform=None,
        query=None,
    )
    assert [item.job_id for item in completed.items] == ["h1", "h2"]
    queued_jobs = {"items": [_JOBS_BODY["items"][1]], "total": 1}
    queued = merge_job_pages(
        queued_jobs,
        {"items": [], "total": 0},
        limit=20,
        offset=0,
        status="queued",
        platform=None,
        query=None,
    )
    assert [item.job_id for item in queued.items] == ["j2"]


def test_pagination_slices_merged_list() -> None:
    page = _merge(limit=2, offset=1)
    assert [item.job_id for item in page.items] == ["j2", "h1"]  # type: ignore[attr-defined]
    assert page.offset == 1 and page.limit == 2  # type: ignore[attr-defined]


def test_total_uses_history_total_when_unfiltered() -> None:
    big_history = {"items": _HISTORY_BODY["items"], "total": 250}
    output = merge_job_pages(
        dict(_JOBS_BODY), big_history, limit=20, offset=0, status=None, platform=None, query=None
    )
    assert output.total == 252
    filtered = merge_job_pages(
        dict(_JOBS_BODY),
        big_history,
        limit=20,
        offset=0,
        status=None,
        platform=None,
        query="选品",
    )
    # Local query filtering only sees the fetched page — honest sample count.
    assert filtered.total == 3


def test_envelope_ok_field_present() -> None:
    output = _merge()
    assert output.ok is True  # type: ignore[attr-defined]
