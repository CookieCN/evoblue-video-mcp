"""The STDIO MCP tool surface: seven frozen tools over the loopback Engine.

Thin adapter (ADR 0001/0004): validate inputs via the frozen schemas, call the
Engine HTTP API with the per-tool timeout budget, translate every failure
through ``mcp.errors``, and answer in the flat ``ok`` envelope as JSON text
content — never ``isError=true`` for domain failures. The ``XxxResult``
RootModel unions are the runtime validators behind that envelope; the tools
advertise no ``outputSchema`` because the protocol requires a top-level
``type: object`` there, which a discriminated-union schema cannot provide
(ADR 0004, active fallback A). Unknown input fields are rejected (contract
通用约定) by tightening the SDK's generated arguments models before
registration.
"""

import json
import logging
import time
from typing import Annotated, Any, Literal, TypeVar

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import ConfigDict, Field, RootModel, ValidationError

from evoblue_video_mcp import __version__
from evoblue_video_mcp.jobs import TERMINAL_JOB_STATUSES, JobStatus
from evoblue_video_mcp.mcp.engine_client import (
    TIMEOUT_CANCEL_S,
    TIMEOUT_DIAGNOSE_LOCAL_S,
    TIMEOUT_DIAGNOSE_NETWORK_S,
    TIMEOUT_LIST_S,
    TIMEOUT_REPORT_S,
    TIMEOUT_SEARCH_S,
    TIMEOUT_STATUS_S,
    TIMEOUT_SUBMIT_S,
    EngineClient,
)
from evoblue_video_mcp.mcp.errors import translate_request_error, translate_status
from evoblue_video_mcp.mcp.listing import (
    EngineListFormatError,
    completed_page,
    single_source_page,
    unified_page,
)
from evoblue_video_mcp.mcp.messages import cancel_message, status_message
from evoblue_video_mcp.mcp.schemas import (
    AnalysisReportResult,
    AnalysisStatusResult,
    CancelAnalysisResult,
    DiagnoseEnvironmentResult,
    ListAnalysisJobsResult,
    ReportSection,
    SearchAnalysisHistoryResult,
    SubmitVideoAnalysisResult,
    ToolError,
)

logger = logging.getLogger(__name__)

_R = TypeVar("_R", bound=RootModel[Any])

_READ_ONLY = ToolAnnotations(read_only_hint=True)
_IDEMPOTENT = ToolAnnotations(idempotent_hint=True)


def _ensure_strict_arguments() -> None:
    """Reject unknown input fields (contract 通用约定).

    The SDK builds each tool's arguments model from the function signature on
    ``create_model(..., __base__=ArgModelBase)``, whose default ``extra``
    behavior silently drops typos. Tightening the base *before* registration
    makes every generated model ``extra='forbid'`` (verified against
    ``mcp`` 2.0.0; the tool tests lock the behavior).
    """
    from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase

    ArgModelBase.model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")


async def _request(
    client: EngineClient,
    method: str,
    path: str,
    *,
    timeout_s: float,
    payload: dict[str, object] | None = None,
    params: dict[str, object] | None = None,
) -> tuple[object | None, ToolError | None]:
    """One Engine round trip → ``(body, None)`` or ``(None, translated error)``."""
    try:
        if method == "GET":
            status, body = await client.get_json(path, params=params, timeout_s=timeout_s)
        else:
            status, body = await client.post_json(path, payload=payload, timeout_s=timeout_s)
    except httpx.RequestError as exc:
        return None, translate_request_error(exc)
    error = translate_status(status, body)
    if error is not None:
        return None, error
    return body, None


#: R4b (review round 2): every list view is a pushed-down server query; the
#: per-request budget below bounds each engine round trip while the monotonic
#: deadline keeps the WHOLE tool call inside TIMEOUT_LIST_S no matter how
#: many segments a view needs (max 2 requests per call by construction).
_LIST_BUDGET_S = float(TIMEOUT_LIST_S)


async def _paged(
    client: EngineClient,
    path: str,
    params: dict[str, object],
    *,
    deadline: float,
) -> tuple[dict[str, object], ToolError | None]:
    """One engine GET within the remaining tool budget."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {}, translate_request_error(
            httpx.TimeoutException("list tool budget exhausted")
        )
    body, error = await _request(
        client,
        "GET",
        path,
        params=params,
        timeout_s=min(_LIST_BUDGET_S, remaining),
    )
    data = body if isinstance(body, dict) else {}
    return data, error


async def _submit(
    client: EngineClient, *, url: str, mode: str, asr: str, language: str | None
) -> SubmitVideoAnalysisResult:
    body, error = await _request(
        client,
        "POST",
        "/api/jobs",
        timeout_s=TIMEOUT_SUBMIT_S,
        payload={"url": url, "mode": mode, "asr": asr, "language": language},
    )
    if error is not None:
        return _validated(
            SubmitVideoAnalysisResult, None, tool="submit_video_analysis", error=error
        )
    data = body if isinstance(body, dict) else {}
    return _validated(
        SubmitVideoAnalysisResult,
        {
            "job_id": data.get("job_id"),
            "status": data.get("status"),
            "next_action": "get_analysis_status",
            "reused": bool(data.get("reused", False)),
        },
        tool="submit_video_analysis",
    )


async def _status(client: EngineClient, *, job_id: str) -> AnalysisStatusResult:
    body, error = await _request(
        client, "GET", f"/api/jobs/{job_id}", timeout_s=TIMEOUT_STATUS_S
    )
    if error is not None:
        return _validated(AnalysisStatusResult, None, tool="get_analysis_status", error=error)
    data = body if isinstance(body, dict) else {}
    current = str(data.get("status"))
    # F3 (feedback #3): the engine synthesizes the root-cause line server-side
    # (real address, no token); the bridge only relays it.
    blocked = data.get("blocked_message")
    message = (
        str(blocked)
        if blocked
        else status_message(current, error_code=data.get("error_code"))
    )
    return _validated(
        AnalysisStatusResult,
        {
            "job_id": data.get("job_id", job_id),
            "status": current,
            "progress": data.get("progress"),
            "stage": data.get("stage"),
            "message": message,
            "retryable": bool(data.get("retryable", False)),
            "error_code": data.get("error_code"),
            "error_detail": data.get("error_detail"),
        },
        tool="get_analysis_status",
    )


async def _report(client: EngineClient, *, job_id: str, section: str) -> AnalysisReportResult:
    body, error = await _request(
        client,
        "GET",
        f"/api/history/{job_id}/report",
        params={"section": section},
        timeout_s=TIMEOUT_REPORT_S,
    )
    if error is not None:
        if error.code == "JOB_NOT_FOUND":
            # A history miss can mean "job not finished yet" — probe the job
            # table so an unfinished task answers REPORT_NOT_READY, not
            # JOB_NOT_FOUND (MCP_TOOLS §3).
            probe_body, probe_error = await _request(
                client, "GET", f"/api/jobs/{job_id}", timeout_s=TIMEOUT_STATUS_S
            )
            if probe_error is None and isinstance(probe_body, dict):
                return _validated(
                    AnalysisReportResult,
                    None,
                    tool="get_analysis_report",
                    error=ToolError(
                        code="REPORT_NOT_READY",
                        message="报告尚未生成, 任务未完成或索引未就绪",
                        retryable=True,
                        detail=None,
                    ),
                )
        return _validated(AnalysisReportResult, None, tool="get_analysis_report", error=error)
    data = body if isinstance(body, dict) else {}
    return _validated(
        AnalysisReportResult,
        {
            "job_id": data.get("job_id", job_id),
            "section": data.get("section", section),
            "markdown": data.get("markdown", ""),
            "file_path": data.get("file_path", ""),
            "schema_version": data.get("schema_version", 1),
            "truncated": bool(data.get("truncated", False)),
        },
        tool="get_analysis_report",
    )


async def _list(
    client: EngineClient,
    *,
    limit: int,
    offset: int,
    status: str | None,
    platform: str | None,
    query: str | None,
) -> ListAnalysisJobsResult:
    """R4b-R9 (review rounds 2-4): every view is a pushed-down server query —
    the engine pages and counts the SAME filtered collection, so no row is
    unreachable behind a fetch cap:
      - explicit non-completed status: ONE /api/jobs query (status/filters/
        pagination pushed);
      - completed: ONE /api/history query (filters/pagination pushed);
      - default: ONE /api/jobs/unified request answering from a single
        SQLite snapshot (segment counts, exclusion, ordering, slice).

    R13/R14/R15 (review rounds 5-6): every branch validates the engine's
    200 body against the frozen REST contract and degrades violations to
    ``ok:false / BRIDGE_INTERNAL`` — never a fake empty page."""
    deadline = time.monotonic() + _LIST_BUDGET_S
    filters: dict[str, object] = {}
    if platform is not None:
        filters["platform"] = platform
    if query is not None:
        filters["query"] = query

    def _fail(error: ToolError | None) -> ListAnalysisJobsResult:
        return _validated(
            ListAnalysisJobsResult, None, tool="list_analysis_jobs", error=error
        )

    def _degrade_to_bridge_internal() -> ListAnalysisJobsResult:
        # A malformed 200 body becomes the frozen envelope (ok:false /
        # BRIDGE_INTERNAL) — never a fake empty page and never a
        # protocol-level error. ``_validated`` with no payload walks its own
        # ValidationError degrade path and logs the tool name only.
        return _validated(ListAnalysisJobsResult, None, tool="list_analysis_jobs")

    if status is not None and status != JobStatus.COMPLETED:
        jobs_body, error = await _paged(
            client,
            "/api/jobs",
            {"limit": limit, "offset": offset, "status": status, **filters},
            deadline=deadline,
        )
        if error is not None:
            return _fail(error)
        try:
            merged = single_source_page(
                jobs_body, status=status, limit=limit, offset=offset
            )
        except EngineListFormatError:
            return _degrade_to_bridge_internal()
        return _validated(ListAnalysisJobsResult, merged.model_dump(), tool="list_analysis_jobs")

    if status == JobStatus.COMPLETED:
        history_body, error = await _paged(
            client, "/api/history", {"limit": limit, "offset": offset, **filters},
            deadline=deadline,
        )
        if error is not None:
            return _fail(error)
        try:
            merged = completed_page(history_body, limit=limit, offset=offset)
        except EngineListFormatError:
            return _degrade_to_bridge_internal()
        return _validated(ListAnalysisJobsResult, merged.model_dump(), tool="list_analysis_jobs")

    # Default view (R9, review round 4): ONE request to the unified engine
    # endpoint. It answers from a single SQLite read transaction — both
    # segment counts, the unfinished-job exclusion, the ordering, and the
    # page slice — so a worker committing a job's completion between the
    # segment reads can never make the same job appear twice (the two-request
    # design could: job row already returned, report newly eligible).
    unified_body, error = await _paged(
        client,
        "/api/jobs/unified",
        {"limit": limit, "offset": offset, **filters},
        deadline=deadline,
    )
    if error is not None:
        return _fail(error)
    try:
        merged = unified_page(unified_body, limit=limit, offset=offset)
    except EngineListFormatError:
        return _degrade_to_bridge_internal()
    return _validated(ListAnalysisJobsResult, merged.model_dump(), tool="list_analysis_jobs")


async def _search(
    client: EngineClient, *, query: str, limit: int, offset: int
) -> SearchAnalysisHistoryResult:
    body, error = await _request(
        client,
        "GET",
        "/api/search",
        params={"q": query, "limit": limit, "offset": offset},
        timeout_s=TIMEOUT_SEARCH_S,
    )
    if error is not None:
        return _validated(
            SearchAnalysisHistoryResult, None, tool="search_analysis_history", error=error
        )
    data = body if isinstance(body, dict) else {}
    items = [
        {
            "job_id": hit.get("job_id"),
            "title": hit.get("title"),
            "matched_fields": hit.get("matched_fields", []),
            "snippet": hit.get("snippet", ""),
        }
        for hit in data.get("items", [])
        if isinstance(hit, dict)
    ]
    return _validated(
        SearchAnalysisHistoryResult,
        {
            "items": items,
            "limit": data.get("limit", limit),
            "offset": data.get("offset", offset),
            "total": data.get("total", 0),
        },
        tool="search_analysis_history",
    )


async def _cancel(client: EngineClient, *, job_id: str) -> CancelAnalysisResult:
    body, error = await _request(
        client, "POST", f"/api/jobs/{job_id}/cancel", timeout_s=TIMEOUT_CANCEL_S
    )
    if error is not None:
        return _validated(CancelAnalysisResult, None, tool="cancel_analysis", error=error)
    data = body if isinstance(body, dict) else {}
    current = str(data.get("status"))
    accepted = current not in TERMINAL_JOB_STATUSES
    return _validated(
        CancelAnalysisResult,
        {
            "job_id": data.get("job_id", job_id),
            "status": current,
            "accepted": accepted,
            "message": cancel_message(current, accepted=accepted),
        },
        tool="cancel_analysis",
    )


async def _diagnose(client: EngineClient, *, include_network: bool) -> DiagnoseEnvironmentResult:
    timeout_s = TIMEOUT_DIAGNOSE_NETWORK_S if include_network else TIMEOUT_DIAGNOSE_LOCAL_S
    body, error = await _request(
        client,
        "GET",
        "/api/diagnostics",
        params={"include_network": include_network},
        timeout_s=timeout_s,
    )
    if error is not None:
        return _validated(
            DiagnoseEnvironmentResult, None, tool="diagnose_environment", error=error
        )
    data = body if isinstance(body, dict) else {}
    return _validated(
        DiagnoseEnvironmentResult,
        {
            "overall": data.get("overall", "fail"),
            "checks": data.get("checks", []),
            "redacted": True,
        },
        tool="diagnose_environment",
    )


def _validated(
    alias: type[_R],
    payload: dict[str, Any] | None,
    *,
    tool: str,
    error: ToolError | None = None,
) -> _R:
    """Wrap a payload or a translated error in the frozen result union.

    A payload that no longer matches the frozen schema (Engine/Bridge version
    skew) degrades to ``BRIDGE_INTERNAL`` — never an unhandled exception, and
    the log carries the tool name only, never payload content (privacy).
    """
    # The discriminator must be explicit: a success payload without "ok" can
    # match neither union arm.
    target: dict[str, Any]
    if error is not None:
        target = {"ok": False, "error": error.model_dump()}
    elif payload is not None:
        target = {"ok": True, **payload}
    else:
        target = {}
    try:
        return alias(target)
    except ValidationError:
        logger.warning("engine payload rejected for %s", tool)
        return alias(
            {
                "ok": False,
                "error": {
                    "code": "BRIDGE_INTERNAL",
                    "message": "Local Engine 响应不符合合同",
                    "retryable": False,
                    "detail": "ValidationError",
                },
            }
        )


def _json(result: RootModel[Any]) -> str:
    """Serialize a validated envelope as the tool's JSON text content.

    Fallback A (ADR 0004): the payload shape is exactly the contract's; the
    transport detail (no ``structuredContent``) is intentional.
    """
    return json.dumps(result.model_dump(), ensure_ascii=False)


def build_bridge_server(
    client: EngineClient,
    *,
    server_name: str = "evoblue-video",
    version: str = __version__,
) -> MCPServer:
    """Assemble the seven-tool MCP server over one Engine client.

    The caller owns ``client``'s lifecycle (``await client.aclose()`` after
    ``server.run()`` returns).
    """
    _ensure_strict_arguments()
    server = MCPServer(
        name=server_name,
        version=version,
        instructions=(
            "EvoBlue Video MCP: 提交视频并获取中文分析报告。提交立即返回 job_id, "
            "用 get_analysis_status 轮询进度, 完成后用 get_analysis_report 按 section 读取。"
        ),
    )

    @server.tool(
        name="submit_video_analysis",
        description="提交视频分析任务, 立即返回 job_id; 同一 URL 的近期任务会被复用 (reused=true)",
        annotations=_IDEMPOTENT,
        structured_output=False,
    )
    async def submit_tool(
        url: Annotated[str, Field(min_length=1, max_length=2048)],
        mode: Literal["auto", "standard", "unboxing"] = "auto",
        asr: Literal["auto", "disabled", "required"] = "auto",
        language: Annotated[str | None, Field(max_length=64)] = None,
    ) -> str:
        return _json(await _submit(client, url=url, mode=mode, asr=asr, language=language))

    @server.tool(
        name="get_analysis_status",
        description="查询任务进度: status/progress/stage/message; 轮询用, 只读",
        annotations=_READ_ONLY,
        structured_output=False,
    )
    async def status_tool(
        job_id: Annotated[str, Field(min_length=1, max_length=64)],
    ) -> str:
        return _json(await _status(client, job_id=job_id))

    @server.tool(
        name="get_analysis_report",
        description="按 section 读取已完成的分析报告 (默认 summary); 长内容会被截断并返回文件路径",
        annotations=_READ_ONLY,
        structured_output=False,
    )
    async def report_tool(
        job_id: Annotated[str, Field(min_length=1, max_length=64)],
        section: ReportSection = "summary",
    ) -> str:
        return _json(await _report(client, job_id=job_id, section=section))

    @server.tool(
        name="list_analysis_jobs",
        description="列出任务: 运行中在前, 之后是已完成的报告历史; 支持分页与 platform/query 过滤",
        annotations=_READ_ONLY,
        structured_output=False,
    )
    async def list_tool(
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        offset: Annotated[int, Field(ge=0)] = 0,
        status: JobStatus | None = None,
        platform: Annotated[str | None, Field(max_length=64)] = None,
        query: Annotated[str | None, Field(max_length=200)] = None,
    ) -> str:
        return _json(
            await _list(
                client,
                limit=limit,
                offset=offset,
                status=str(status) if status is not None else None,
                platform=platform,
                query=query,
            )
        )

    @server.tool(
        name="search_analysis_history",
        description="全文搜索历史报告 (标题/作者/URL/标签/摘要/字幕), 返回命中片段",
        annotations=_READ_ONLY,
        structured_output=False,
    )
    async def search_tool(
        query: Annotated[str, Field(min_length=1, max_length=500)],
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> str:
        return _json(
            await _search(client, query=query, limit=limit, offset=offset)
        )

    @server.tool(
        name="cancel_analysis",
        description="取消一个任务; 运行中的任务会在安全点生效, 终态任务幂等返回 accepted=false",
        annotations=_IDEMPOTENT,
        structured_output=False,
    )
    async def cancel_tool(
        job_id: Annotated[str, Field(min_length=1, max_length=64)],
    ) -> str:
        return _json(await _cancel(client, job_id=job_id))

    @server.tool(
        name="diagnose_environment",
        description="诊断本地环境 (数据库/报告目录/FFmpeg/yt-dlp/LLM/ASR/磁盘); 单项失败不影响整体",
        annotations=_READ_ONLY,
        structured_output=False,
    )
    async def diagnose_tool(
        include_network: bool = False,
    ) -> str:
        return _json(await _diagnose(client, include_network=include_network))

    return server
