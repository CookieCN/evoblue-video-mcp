"""Two-source merge for ``list_analysis_jobs`` (MCP_TOOLS §4, ADR 0004).

Running (non-terminal) jobs come from ``/api/jobs``; completed history comes
from ``/api/history``. Active entries lead, ``job_id`` dedup favors the active
entry, and ``platform``/``query`` only filter the history source (the Job
table has no platform/title dimension). Pure functions over parsed REST bodies
so unit tests need no HTTP at all.
"""

from typing import Any

from evoblue_video_mcp.jobs import ACTIVE_JOB_STATUSES, JobStatus
from evoblue_video_mcp.mcp.schemas import AnalysisJobListItem, ListAnalysisJobsOutput

_ACTIVE_STATUS_VALUES = frozenset(status.value for status in ACTIVE_JOB_STATUSES)

#: Page cap for each source fetch; the merged output page is sliced locally.
#: A single-user local Engine is comfortably inside this (frozen max page 100).
_SOURCE_FETCH_LIMIT = 100


def _is_active(status_value: str) -> bool:
    return status_value in _ACTIVE_STATUS_VALUES


def _active_item(raw: dict[str, Any]) -> AnalysisJobListItem:
    progress = raw.get("progress")
    return AnalysisJobListItem(
        job_id=str(raw["job_id"]),
        title=None,
        platform=None,
        status=JobStatus(str(raw["status"])),
        progress=int(progress) if isinstance(progress, (int, float)) else None,
    )


def _history_item(raw: dict[str, Any]) -> AnalysisJobListItem:
    return AnalysisJobListItem(
        job_id=str(raw["job_id"]),
        title=str(raw["title"]) if raw.get("title") else None,
        platform=str(raw["platform"]) if raw.get("platform") else None,
        status=JobStatus.COMPLETED,
        progress=100,
    )


def _history_passes_filters(
    raw: dict[str, Any], *, platform: str | None, query: str | None
) -> bool:
    if platform is not None and str(raw.get("platform") or "").lower() != platform.lower():
        return False
    if query is not None:
        title = str(raw.get("title") or "")
        if query.lower() not in title.lower():
            return False
    return True


def merge_job_pages(
    jobs_body: dict[str, Any],
    history_body: dict[str, Any],
    *,
    limit: int,
    offset: int,
    status: str | None,
    platform: str | None,
    query: str | None,
) -> ListAnalysisJobsOutput:
    """Merge one ``/api/jobs`` page and one ``/api/history`` page.

    ``status`` filtering is pushed down to ``/api/jobs`` server-side; the
    history source counts as ``completed`` and is included only when the
    filter is ``None`` or ``completed``.
    """
    active_raw = [
        item
        for item in jobs_body.get("items", [])
        if isinstance(item, dict) and _is_active(str(item.get("status")))
    ]
    include_history = status is None or status == JobStatus.COMPLETED
    history_raw = [
        item
        for item in history_body.get("items", [])
        if isinstance(item, dict) and include_history
    ]
    history_raw = [
        item
        for item in history_raw
        if _history_passes_filters(item, platform=platform, query=query)
    ]

    active_items = [_active_item(item) for item in active_raw]
    history_items = [_history_item(item) for item in history_raw]

    active_ids = {item.job_id for item in active_items}
    deduped_history = [item for item in history_items if item.job_id not in active_ids]
    merged = active_items + deduped_history

    if query is None:
        # history_body["total"] covers records beyond the fetched page; the
        # dedup subtraction only knows about the overlap actually fetched.
        total = len(active_items) + int(history_body.get("total") or 0) - (
            len(history_items) - len(deduped_history)
        )
    else:
        # Query filtering happened locally over the fetched page only.
        total = len(merged)

    page = merged[offset : offset + limit]
    return ListAnalysisJobsOutput(items=page, limit=limit, offset=offset, total=total)
