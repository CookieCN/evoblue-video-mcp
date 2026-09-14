"""Server-paged page builders for ``list_analysis_jobs`` (MCP_TOOLS §4, ADR 0004).

R4b-R9 (review rounds 2-4): every view is a PUSHED-DOWN server query — the
engine pages and counts the same filtered collection, so no row is unreachable
and no fixed fetch cap exists:
  - explicit non-completed status: one /api/jobs query (status + filters +
    limit/offset pushed); terminal rows pass through with error_code/url.
  - completed: one /api/history query (filters + limit/offset pushed).
  - default: ONE /api/jobs/unified request — both segment counts, the
    unfinished-job exclusion, the ordering, and the page slice come from a
    single SQLite snapshot (``unified_page``).

R13-R17 (review rounds 5-7): ALL three sources share ONE strict validation
boundary, and every item is validated against a WIRE-LEVEL model mirroring
the engine's REST schema (Pydantic strict mode — no type coercion, unknown
future fields ignored) BEFORE being projected to the MCP output schema.
Violations — missing/wrong-typed required fields, a non-object item, a
window that does not echo the request, an impossible page capacity,
inconsistent segment counts, a row outside its segment or in the wrong
segment POSITION — raise :class:`EngineListFormatError`; the bridge degrades
to ``ok:false / BRIDGE_INTERNAL`` (isError=false). Lenient defaults and
silent coercion are forbidden on purpose: they masquerade version skew and
engine bugs as "no data" or as fixed-up rows. Pure functions over parsed
REST bodies so unit tests need no HTTP at all.
"""

from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.mcp.schemas import AnalysisJobListItem, ListAnalysisJobsOutput

_WireModelT = TypeVar("_WireModelT", bound="_WireModel")


class EngineListFormatError(ValueError):
    """An engine list-page body violates the frozen contract (version skew
    or an engine bug) — the bridge must degrade to ``BRIDGE_INTERNAL``,
    never answer a fake empty page nor raise a protocol-level error."""


class _WireModel(BaseModel):
    """Strict wire validation: exact types only, unknown fields ignored.

    ``strict=True`` forbids every coercion (int stays int, str stays str,
    bool is not an int); ``extra="ignore"`` tolerates fields a FUTURE engine
    adds without breaking an older bridge — version skew only degrades on
    genuinely invalid payloads.
    """

    model_config = ConfigDict(extra="ignore", strict=True)


class _WireJobRow(_WireModel):
    """Mirrors ``web.schemas.JobListItem`` (the /api/jobs item)."""

    job_id: str
    status: str
    progress: int = Field(ge=0)
    created_at: float
    stage: str | None = None
    error_code: str | None = None
    title: str | None = None
    platform: str | None = None
    url: str | None = None


class _WireHistoryRow(_WireModel):
    """Mirrors ``web.schemas.HistoryItem`` (the /api/history item)."""

    job_id: str
    analysis_id: str
    title: str
    platform: str
    author: str
    video_id: str
    source_url: str
    published_at: float | None = None
    analyzed_at: float
    language: str
    summary_mode: str
    asr_provider: str
    asr_model: str
    asr_model_version: str
    tags: list[str]
    summary_preview: str
    file_path: str
    content_hash: str
    doc_status: str
    indexed_at: float


class _WireUnifiedRow(_WireModel):
    """Mirrors ``web.schemas.UnifiedJobListItem`` (the /api/jobs/unified
    item — the slim unified projection, not the full HistoryItem)."""

    kind: str
    job_id: str
    status: str
    progress: int = Field(ge=0)
    stage: str | None = None
    error_code: str | None = None
    created_at: float | None = None
    title: str | None = None
    platform: str | None = None
    url: str | None = None


def _wire(model: type[_WireModelT], raw: Any) -> _WireModelT:
    """Validate one wire row; any violation is a contract error."""
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise EngineListFormatError(
            "engine list item failed wire validation"
        ) from exc


def _mcp_job(wire: _WireJobRow | _WireUnifiedRow) -> AnalysisJobListItem:
    try:
        status = JobStatus(wire.status)
    except ValueError as exc:
        raise EngineListFormatError("engine list item has an unknown status") from exc
    try:
        return AnalysisJobListItem(
            job_id=wire.job_id,
            title=wire.title,
            platform=wire.platform,
            status=status,
            progress=wire.progress,
            error_code=wire.error_code,
            url=wire.url,
        )
    except ValidationError as exc:
        # wire-legal but MCP-illegal values (e.g. progress>100: the REST
        # contract caps at >=0, the MCP output at 100) stay inside the
        # boundary — they degrade, never escape as a protocol error.
        raise EngineListFormatError(
            "engine list item failed MCP projection"
        ) from exc


def _mcp_history_item(
    job_id: str,
    title: str | None,
    platform: str | None,
    url: str | None,
) -> AnalysisJobListItem:
    try:
        return AnalysisJobListItem(
            job_id=job_id,
            title=title,
            platform=platform,
            status=JobStatus.COMPLETED,
            progress=100,
            url=url,
        )
    except ValidationError as exc:
        raise EngineListFormatError(
            "engine list item failed MCP projection"
        ) from exc


def _strict_window(
    body: dict[str, Any], *, limit: int, offset: int
) -> tuple[list[Any], int]:
    """Validate the page-window contract shared by every engine list source.

    Required: ``items`` list, non-negative int ``total``, and a window that
    echoes the request (``limit``/``offset`` — bools rejected explicitly,
    Python's ``True == 1`` would otherwise slip through the equality). The
    page capacity must be exact: a well-formed engine returns
    ``min(limit, max(0, total-offset))`` rows — an empty FIRST page with a
    positive total, or an over-capacity page, is a contract violation.
    """
    if not isinstance(body, dict):
        raise EngineListFormatError("engine list body is not an object")
    raw_items = body.get("items")
    total = body.get("total")
    if (
        not isinstance(raw_items, list)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
    ):
        raise EngineListFormatError("engine list body missing items/total")
    resp_limit = body.get("limit")
    resp_offset = body.get("offset")
    if (
        not isinstance(resp_limit, int)
        or isinstance(resp_limit, bool)
        or not isinstance(resp_offset, int)
        or isinstance(resp_offset, bool)
        or resp_limit != limit
        or resp_offset != offset
    ):
        raise EngineListFormatError("engine list window does not echo the request")
    expected_rows = min(limit, max(0, total - offset))
    if len(raw_items) != expected_rows:
        raise EngineListFormatError("engine list page capacity is inconsistent")
    return raw_items, total


def single_source_page(
    jobs_body: dict[str, Any],
    *,
    status: str,
    limit: int,
    offset: int,
) -> ListAnalysisJobsOutput:
    """Explicit non-completed status: the engine-queried page passes through.

    The server already applied status/platform/query/limit/offset, so the
    body IS the requested page and its ``total`` counts the same filtered
    collection (R4). R14/R16: strict window + wire-level item validation; an
    item whose status does not match the requested filter is a violation,
    not a row to silently drop.
    """
    raw_items, total = _strict_window(jobs_body, limit=limit, offset=offset)
    page_items: list[AnalysisJobListItem] = []
    for raw in raw_items:
        item = _mcp_job(_wire(_WireJobRow, raw))
        if item.status.value != status:
            raise EngineListFormatError(
                "engine list item status does not match the requested filter"
            )
        page_items.append(item)
    return ListAnalysisJobsOutput(
        items=page_items, limit=limit, offset=offset, total=total
    )


def completed_page(
    history_body: dict[str, Any],
    *,
    limit: int,
    offset: int,
) -> ListAnalysisJobsOutput:
    """status=completed: the report-history endpoint is the whole answer,
    paged and counted server-side (R4b — row 5001 is one request away).
    R14/R16: strict window + the FULL HistoryItem wire contract per row."""
    raw_items, total = _strict_window(history_body, limit=limit, offset=offset)
    page_items = [
        _mcp_history_item(w.job_id, w.title, w.platform, w.source_url)
        for w in (_wire(_WireHistoryRow, raw) for raw in raw_items)
    ]
    return ListAnalysisJobsOutput(
        items=page_items, limit=limit, offset=offset, total=total
    )


def unified_page(
    body: dict[str, Any],
    *,
    limit: int,
    offset: int,
) -> ListAnalysisJobsOutput:
    """Default view (R9, review round 4): the unified endpoint's page passes
    through — the engine answered counts, exclusion, ordering, and slicing
    from ONE SQLite snapshot, so ``total`` is exact on every page and the
    same job can never appear in both segments.

    R15/R17 (review rounds 6-7): the FULL response contract is validated —
    segment counts are required and must sum to ``total``; every row's kind
    must match its GLOBAL position (``offset + index < jobs_total`` is a job
    row, beyond it a history row — correct totals alone do not prove the
    slice is ordered); job rows may not be completed (completed jobs live in
    the history segment); history rows must present as completed. Any
    violation degrades to ``BRIDGE_INTERNAL`` instead of correcting or
    ignoring engine data.
    """
    raw_items, total = _strict_window(body, limit=limit, offset=offset)
    jobs_total = body.get("jobs_total")
    history_total = body.get("history_total")
    for segment_total in (jobs_total, history_total):
        if (
            not isinstance(segment_total, int)
            or isinstance(segment_total, bool)
            or segment_total < 0
        ):
            raise EngineListFormatError("unified list body missing segment totals")
    if not isinstance(jobs_total, int) or not isinstance(history_total, int):
        raise EngineListFormatError("unified list body missing segment totals")
    if total != jobs_total + history_total:
        raise EngineListFormatError("unified total does not equal the segment sums")
    page_items: list[AnalysisJobListItem] = []
    for index, raw in enumerate(raw_items):
        wire = _wire(_WireUnifiedRow, raw)
        expected_kind = "job" if offset + index < jobs_total else "history"
        if wire.kind != expected_kind:
            raise EngineListFormatError(
                "unified row sits outside its segment position "
                f"(global index {offset + index}, expected {expected_kind})"
            )
        if wire.kind == "history":
            if wire.status != JobStatus.COMPLETED.value:
                raise EngineListFormatError(
                    "unified history item must present as completed"
                )
            page_items.append(
                _mcp_history_item(wire.job_id, wire.title, wire.platform, wire.url)
            )
        else:
            item = _mcp_job(wire)
            if item.status == JobStatus.COMPLETED:
                raise EngineListFormatError(
                    "unified job segment must not contain completed jobs"
                )
            page_items.append(item)
    return ListAnalysisJobsOutput(
        items=page_items, limit=limit, offset=offset, total=total
    )
