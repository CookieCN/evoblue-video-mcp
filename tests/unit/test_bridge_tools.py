"""P4-004: the seven tools through ``server.call_tool`` with a scripted Engine.

Every domain failure must arrive as a normal structured result with
``is_error=False`` and ``ok=False`` (ADR 0004); only protocol-level input
validation may set ``is_error=True``.
"""

import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

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
from evoblue_video_mcp.mcp.schemas import TOOL_NAMES
from evoblue_video_mcp.mcp.server import build_bridge_server


class ScriptedHttp:
    """Scripted Engine responses; each call pops one entry."""

    def __init__(self, script: list[httpx.Response | Exception]) -> None:
        self._script = list(script)
        self.calls: list[tuple[str, str, object, float]] = []

    async def get(
        self, url: str, *, params: object = None, headers: object = None, timeout: object = None
    ) -> httpx.Response:
        self.calls.append(("GET", url, params, float(timeout)))  # type: ignore[arg-type]
        return self._next()

    async def post(
        self, url: str, *, json: object = None, headers: object = None, timeout: object = None
    ) -> httpx.Response:
        self.calls.append(("POST", url, json, float(timeout)))  # type: ignore[arg-type]
        return self._next()

    def _next(self) -> httpx.Response:
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _http_200(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)


def _payload(result: Any) -> dict[str, Any]:
    """Fallback A: the flat envelope arrives as JSON text content."""
    return json.loads(result.content[0].text)


def _build(script: list[httpx.Response | Exception]) -> tuple[object, ScriptedHttp]:
    http = ScriptedHttp(script)
    client = EngineClient(http, base_url="http://127.0.0.1:8765", token=None)
    return build_bridge_server(client), http


async def _call(server: object, name: str, args: dict[str, Any]) -> Any:
    return await server.call_tool(name, args)  # type: ignore[attr-defined]


async def test_server_registers_exactly_the_seven_contract_tools() -> None:
    server, _ = _build([])
    tools = await server.list_tools()  # type: ignore[attr-defined]
    assert sorted(tool.name for tool in tools) == sorted(TOOL_NAMES)


async def test_submit_happy_path_is_flat_envelope_with_budget() -> None:
    server, http = _build([_http_200({"job_id": "01J", "status": "queued", "reused": False})])
    result = await _call(
        server,
        "submit_video_analysis",
        {"url": "https://www.youtube.com/watch?v=abc", "mode": "auto"},
    )
    assert result.is_error is False
    assert _payload(result)["ok"] is True
    assert _payload(result)["next_action"] == "get_analysis_status"
    assert json.loads(result.content[0].text) == _payload(result)
    method, url, payload, budget = http.calls[0]
    assert method == "POST" and url.endswith("/api/jobs")
    assert payload["url"] == "https://www.youtube.com/watch?v=abc"
    assert budget == TIMEOUT_SUBMIT_S


async def test_submit_reused_running_job_reports_current_status() -> None:
    server, _ = _build(
        [_http_200({"job_id": "01J", "status": "summarizing_chunks", "reused": True})]
    )
    result = await _call(server, "submit_video_analysis", {"url": "https://x.example/v"})
    assert result.is_error is False
    assert _payload(result)["status"] == "summarizing_chunks"
    assert _payload(result)["reused"] is True


async def test_engine_down_answers_not_ready_as_normal_result() -> None:
    server, _ = _build([httpx.ConnectError("refused")])
    result = await _call(server, "submit_video_analysis", {"url": "https://x.example/v"})
    assert result.is_error is False  # domain failure, not protocol error
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "ENGINE_NOT_READY"
    assert payload["error"]["retryable"] is True


async def test_budget_timeout_maps_to_engine_timeout() -> None:
    server, http = _build([httpx.ReadTimeout("late")])
    result = await _call(server, "get_analysis_status", {"job_id": "01J"})
    assert result.is_error is False
    assert _payload(result)["error"]["code"] == "ENGINE_TIMEOUT"
    assert http.calls[0][3] == TIMEOUT_STATUS_S


async def test_status_synthesizes_chinese_message() -> None:
    server, _ = _build(
        [
            _http_200(
                {
                    "job_id": "01J",
                    "status": "summarizing_chunks",
                    "stage": "summarizing_chunks",
                    "progress": 72,
                    "retryable": False,
                    "error_code": None,
                    "error_detail": None,
                }
            )
        ]
    )
    result = await _call(server, "get_analysis_status", {"job_id": "01J"})
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["progress"] == 72
    assert payload["message"] == "正在整理视频内容"
    assert payload["stage"] == "summarizing_chunks"


async def test_status_404_maps_to_job_not_found() -> None:
    server, _ = _build([httpx.Response(404, json={"detail": "job not found"})])
    result = await _call(server, "get_analysis_status", {"job_id": "ghost"})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "JOB_NOT_FOUND"
    assert result.is_error is False


async def test_report_section_passes_through_with_truncation_flag() -> None:
    server, http = _build(
        [
            _http_200(
                {
                    "job_id": "01J",
                    "section": "summary",
                    "markdown": "## 核心摘要\n\n内容",
                    "file_path": "D:\\Reports\\video.md",
                    "schema_version": 1,
                    "truncated": True,
                }
            )
        ]
    )
    result = await _call(
        server, "get_analysis_report", {"job_id": "01J", "section": "summary"}
    )
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["markdown"].startswith("## 核心摘要")
    assert payload["truncated"] is True
    assert http.calls[0][3] == TIMEOUT_REPORT_S


async def test_report_history_miss_with_live_job_answers_not_ready() -> None:
    server, http = _build(
        [
            httpx.Response(
                404, json={"error": {"code": "HISTORY_ITEM_NOT_FOUND", "message": "m"}}
            ),
            _http_200({"job_id": "01J", "status": "generating_report", "progress": 80}),
        ]
    )
    result = await _call(server, "get_analysis_report", {"job_id": "01J"})
    payload = _payload(result)
    assert payload["error"]["code"] == "REPORT_NOT_READY"
    assert payload["error"]["retryable"] is True
    assert len(http.calls) == 2  # history miss then job probe


async def test_report_history_miss_without_job_answers_not_found() -> None:
    server, _ = _build(
        [
            httpx.Response(
                404, json={"error": {"code": "HISTORY_ITEM_NOT_FOUND", "message": "m"}}
            ),
            httpx.Response(404, json={"detail": "job not found"}),
        ]
    )
    result = await _call(server, "get_analysis_report", {"job_id": "ghost"})
    assert _payload(result)["error"]["code"] == "JOB_NOT_FOUND"


async def test_list_merges_sources_with_search_budget() -> None:
    # R9: the default view is ONE unified request; the page arrives
    # pre-merged (kind-tagged) with the exact snapshot total.
    unified = {
        "items": [
            {"kind": "job", "job_id": "j1", "status": "queued", "progress": 0},
            {
                "kind": "history",
                "job_id": "h1",
                "title": "T",
                "platform": "youtube",
                "status": "completed",
                "progress": 100,
            },
        ],
        "total": 2,
        "jobs_total": 1,
        "history_total": 1,
        "limit": 20,
        "offset": 0,
    }
    server, http = _build([_http_200(unified)])
    result = await _call(server, "list_analysis_jobs", {"limit": 20, "offset": 0})
    payload = _payload(result)
    assert payload["ok"] is True
    assert [item["job_id"] for item in payload["items"]] == ["j1", "h1"]
    assert payload["total"] == 2
    assert all(call[3] == TIMEOUT_LIST_S for call in http.calls)


async def test_search_maps_hit_fields_and_passes_query() -> None:
    engine_body = {
        "items": [
            {
                "job_id": "h1",
                "title": "选品",
                "platform": "youtube",
                "analyzed_at": 1.0,
                "snippet": "[选品] 方法",
                "matched_fields": ["title"],
                "doc_status": "active",
            }
        ],
        "total": 1,
        "limit": 10,
        "offset": 0,
    }
    server, http = _build([_http_200(engine_body)])
    result = await _call(
        server, "search_analysis_history", {"query": "选品", "limit": 10, "offset": 0}
    )
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["items"][0]["matched_fields"] == ["title"]
    assert "platform" not in payload["items"][0]  # MCP shape is the frozen subset
    params = http.calls[0][2]
    assert params["q"] == "选品"
    assert http.calls[0][3] == TIMEOUT_SEARCH_S


async def test_search_index_failure_stays_retryable() -> None:
    server, _ = _build(
        [
            httpx.Response(
                503, json={"error": {"code": "SEARCH_INDEX_UNAVAILABLE", "message": "d"}}
            )
        ]
    )
    result = await _call(server, "search_analysis_history", {"query": "x"})
    payload = _payload(result)
    assert payload["error"]["code"] == "SEARCH_INDEX_UNAVAILABLE"
    assert payload["error"]["retryable"] is True


async def test_cancel_semantics_across_job_states() -> None:
    # running job: accepted, message points to safe point
    server, http = _build(
        [_http_200({"job_id": "01J", "status": "downloading_audio", "progress": 10})]
    )
    result = await _call(server, "cancel_analysis", {"job_id": "01J"})
    payload = _payload(result)
    assert payload["accepted"] is True
    assert payload["status"] == "downloading_audio"
    assert "安全点" in payload["message"]
    assert http.calls[0][3] == TIMEOUT_CANCEL_S

    # terminal job: idempotent accepted=false
    server, _ = _build([_http_200({"job_id": "01J", "status": "completed"})])
    result = await _call(server, "cancel_analysis", {"job_id": "01J"})
    payload = _payload(result)
    assert payload["accepted"] is False
    assert "completed" in payload["message"]


async def test_diagnose_maps_checks_gates_network_budget() -> None:
    checks = [
        {"name": "local_engine", "status": "pass", "message": "Local Engine 在线", "detail": None},
        {"name": "llm_api", "status": "skipped", "message": "未请求网络诊断", "detail": None},
    ]
    server, http = _build(
        [_http_200({"engine_version": "1.2.3", "overall": "warning", "checks": checks})]
    )
    result = await _call(server, "diagnose_environment", {})
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["overall"] == "warning"
    assert payload["redacted"] is True
    assert "engine_version" not in payload  # HTTP-only field stays HTTP-only
    assert http.calls[0][3] == TIMEOUT_DIAGNOSE_LOCAL_S
    assert http.calls[0][2]["include_network"] == "False"  # wire format (query string)

    server, http = _build(
        [_http_200({"engine_version": "1.2.3", "overall": "pass", "checks": []})]
    )
    await _call(server, "diagnose_environment", {"include_network": True})
    assert http.calls[0][3] == TIMEOUT_DIAGNOSE_NETWORK_S


async def test_unknown_input_field_is_rejected_at_protocol_level() -> None:
    server, _ = _build([])
    # In-process call_tool surfaces SDK validation as a raised ToolError; over
    # the wire the stdio layer turns it into an isError=true frame (proven in
    # the P4-005 stdio integration test).
    with pytest.raises(ToolError, match="Extra inputs are not permitted"):
        await _call(server, "get_analysis_status", {"job_id": "01J", "bogus_field": 1})


async def test_malformed_engine_payload_degrades_to_bridge_internal() -> None:
    server, _ = _build([_http_200({"unexpected": "shape"})])
    result = await _call(server, "get_analysis_status", {"job_id": "01J"})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"
    assert result.is_error is False


async def test_status_relays_engine_blocked_message() -> None:
    """F3 (#3): the engine synthesizes the root-cause line; the bridge relays
    it instead of the generic queued message."""
    server, _ = _build(
        [
            _http_200(
                {
                    "job_id": "01J",
                    "status": "waiting_for_model",
                    "stage": None,
                    "progress": 0,
                    "retryable": False,
                    "error_code": None,
                    "error_detail": None,
                    "blocked_reason": "waiting_for_model",
                    "blocked_message": (
                        "等待本地语音模型就绪: 到 WebUI 模型页安装 sensevoice-small-int8 "
                        "(http://127.0.0.1:8765/models), 安装完成后任务自动继续"
                    ),
                }
            )
        ]
    )
    result = await _call(server, "get_analysis_status", {"job_id": "01J"})
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["message"].startswith("等待本地语音模型就绪")
    assert "/models" in payload["message"]


async def test_list_carries_active_job_identity() -> None:
    """F3 (#9): active entries carry title/platform from the jobs row (the
    unified endpoint's kind-tagged page passes through)."""
    server, _ = _build(
        [
            _http_200(
                {
                    "items": [
                        {
                            "kind": "job",
                            "job_id": "01J",
                            "status": "waiting_for_model",
                            "stage": None,
                            "progress": 0,
                            "error_code": None,
                            "created_at": 1000.0,
                            "title": "官方双语视频",
                            "platform": "bilibili",
                            "url": "https://www.bilibili.com/video/BV1GJ411x7h7",
                        }
                    ],
                    "total": 1,
                    "jobs_total": 1,
                    "history_total": 0,
                    "limit": 20,
                    "offset": 0,
                }
            )
        ]
    )
    result = await _call(
        server, "list_analysis_jobs", {"limit": 20, "offset": 0}
    )
    payload = _payload(result)
    assert payload["items"][0]["title"] == "官方双语视频"
    assert payload["items"][0]["platform"] == "bilibili"


async def test_list_explicit_status_pushes_pagination_down() -> None:
    """R4 (review): an explicit terminal filter is ONE server-side query with
    limit/offset (and platform/query) pushed down — page 6 of 101 failures
    returns rows, not an empty window sliced from a first fetch."""
    page = {
        "items": [
            {
                "job_id": "f100",
                "status": "failed",
                "error_code": "X",
                "progress": 0,
                "created_at": 1000.0,
            }
        ],
        "total": 101,
        "limit": 20,
        "offset": 100,
    }
    server, http = _build([_http_200(page)])
    result = await _call(
        server, "list_analysis_jobs", {"status": "failed", "offset": 100, "limit": 20}
    )
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["total"] == 101
    assert len(payload["items"]) == 1  # the 101st row — the exact tail page
    assert payload["items"][0]["job_id"] == "f100"
    # exactly ONE engine call, carrying the pushed-down pagination
    assert len(http.calls) == 1
    method, url, params, _timeout = http.calls[0]
    assert method == "GET" and str(url).endswith("/api/jobs")
    assert params == {"limit": "20", "offset": "100", "status": "failed"}

    server, http = _build(
        [_http_200({"items": [], "total": 0, "limit": 5, "offset": 0})]
    )
    await _call(
        server,
        "list_analysis_jobs",
        {"status": "cancelled", "platform": "bilibili", "query": "测试", "limit": 5},
    )
    assert http.calls[0][2] == {
        "limit": "5",
        "offset": "0",
        "status": "cancelled",
        "platform": "bilibili",
        "query": "测试",
    }


async def test_list_default_view_one_unified_request_spans_segments() -> None:
    """R9 (review round 4): the default view is ONE request to the unified
    engine endpoint — the engine pages both segments inside a single SQLite
    snapshot; the bridge passes the page through with its exact total."""
    unified_page_body = {
        "items": [
            {"kind": "job", "job_id": "j98", "status": "failed", "progress": 0},
            {"kind": "job", "job_id": "j99", "status": "queued", "progress": 0},
            {
                "kind": "history",
                "job_id": "h0",
                "title": "T",
                "platform": "youtube",
                "status": "completed",
                "progress": 100,
            },
            {
                "kind": "history",
                "job_id": "h1",
                "title": "T",
                "platform": "bilibili",
                "status": "completed",
                "progress": 100,
            },
        ],
        "total": 101 + 5001,
        "jobs_total": 100,
        "history_total": 5002,
        "limit": 4,
        "offset": 98,
    }
    server, http = _build([_http_200(unified_page_body)])
    result = await _call(server, "list_analysis_jobs", {"offset": 98, "limit": 4})
    payload = _payload(result)
    assert payload["ok"] is True
    assert [i["job_id"] for i in payload["items"]] == ["j98", "j99", "h0", "h1"]
    assert payload["items"][0]["status"] == "failed"
    assert payload["items"][2]["status"] == "completed"
    assert payload["total"] == 101 + 5001
    assert len(http.calls) == 1, "exactly ONE unified request"
    method, url, params, _t = http.calls[0]
    assert method == "GET" and str(url).endswith("/api/jobs/unified")
    assert params == {"limit": "4", "offset": "98"}


async def test_list_default_view_full_jobs_page_reports_history_total() -> None:
    """R6/R9: a page that lies ENTIRELY inside the jobs segment still reports
    the exact merged total — the unified endpoint counts both segments in the
    same snapshot, so clients never stop paging at the segment edge."""
    unified_page_body = {
        "items": [{"kind": "job", "job_id": "j0", "status": "queued", "progress": 0}],
        "total": 2 + 5001,
        "jobs_total": 2,
        "history_total": 5001,
        "limit": 1,
        "offset": 0,
    }
    server, http = _build([_http_200(unified_page_body)])
    result = await _call(server, "list_analysis_jobs", {"offset": 0, "limit": 1})
    payload = _payload(result)
    assert payload["ok"] is True
    assert [i["job_id"] for i in payload["items"]] == ["j0"]
    assert payload["total"] == 2 + 5001
    assert len(http.calls) == 1


class _UnifiedEngineHttp:
    """Stateful pager for /api/jobs/unified honouring the one-snapshot
    contract: a single pre-merged, strictly disjoint row list with exact
    window echo and segment totals."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._jobs_total = sum(1 for r in rows if r.get("kind") == "job")
        self._history_total = sum(1 for r in rows if r.get("kind") == "history")
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(
        self, url: str, *, params: object = None, headers: object = None, timeout: object = None
    ) -> httpx.Response:
        p = {str(k): str(v) for k, v in dict(params or {}).items()}  # type: ignore[arg-type]
        self.calls.append((str(url), p))
        assert str(url).endswith("/api/jobs/unified"), (
            "the default view must page the unified endpoint only (R9)"
        )
        limit = int(p.get("limit", 20))
        offset = int(p.get("offset", 0))
        items = [dict(r) for r in self._rows[offset : offset + limit]]
        return httpx.Response(
            200,
            json={
                "items": items,
                "total": len(self._rows),
                "jobs_total": self._jobs_total,
                "history_total": self._history_total,
                "limit": limit,
                "offset": offset,
            },
        )


async def test_list_default_view_walks_all_rows_without_gaps_or_duplicates() -> None:
    """R6/R9: continuous paging returns every row exactly once with the exact
    merged total on every page — including jobs whose reports are indexed
    while the job row is still non-completed (they appear once, as the job)."""
    rows = [
        {"kind": "job", "job_id": "run1", "status": "transcribing", "progress": 30},
        {"kind": "job", "job_id": "dup", "status": "failed", "error_code": "X", "progress": 0},
        {"kind": "job", "job_id": "run2", "status": "queued", "progress": 0},
        {"kind": "job", "job_id": "dup2", "status": "failed", "error_code": "Y", "progress": 0},
        {"kind": "history", "job_id": "h1", "title": "T",
         "platform": "youtube", "status": "completed", "progress": 100},
        {"kind": "history", "job_id": "h2", "title": "T",
         "platform": "bilibili", "status": "completed", "progress": 100},
        {"kind": "history", "job_id": "h3", "title": "T",
         "platform": "youtube", "status": "completed", "progress": 100},
    ]
    http = _UnifiedEngineHttp(rows)
    client = EngineClient(http, base_url="http://127.0.0.1:8765", token=None)
    server = build_bridge_server(client)

    seen: list[str] = []
    offset = 0
    while True:
        result = await _call(server, "list_analysis_jobs", {"offset": offset, "limit": 2})
        payload = _payload(result)
        assert payload["ok"] is True
        assert payload["total"] == 7, "exact merged total on every page (R6/R9)"
        seen.extend(i["job_id"] for i in payload["items"])
        offset += 2
        if offset >= payload["total"]:
            break
    assert seen == ["run1", "dup", "run2", "dup2", "h1", "h2", "h3"]
    assert len(set(seen)) == 7, "no row may appear twice across pages"
    assert len(http.calls) == 4, "one unified request per page"


async def test_list_default_view_pushes_filters_and_deep_offset() -> None:
    """R6/R8/R9: platform/query filters and the global offset are pushed to
    the unified endpoint — row 5005 of a filtered view is one request."""
    deep_page = {
        "items": [
            {
                "kind": "history",
                "job_id": "h5004",
                "title": "T",
                "platform": "youtube",
                "status": "completed",
                "progress": 100,
            }
        ],
        "total": 5005,
        "jobs_total": 4,
        "history_total": 5001,
        "limit": 20,
        "offset": 5004,
    }
    server, http = _build([_http_200(deep_page)])
    result = await _call(
        server,
        "list_analysis_jobs",
        {"offset": 5004, "limit": 20, "platform": "youtube"},
    )
    payload = _payload(result)
    assert payload["ok"] is True
    assert [i["job_id"] for i in payload["items"]] == ["h5004"]
    assert payload["total"] == 5005
    assert len(http.calls) == 1
    _m, url, params, _t = http.calls[0]
    assert str(url).endswith("/api/jobs/unified")
    assert params == {"limit": "20", "offset": "5004", "platform": "youtube"}


async def test_explicit_status_malformed_body_degrades_to_bridge_internal() -> None:
    """R14 (review round 6, P2): explicit status views share the strict
    validation boundary — completed and failed both answer ok:false /
    BRIDGE_INTERNAL for a malformed 200 body, never a fake empty page."""
    server, _ = _build([_http_200({"unexpected": "shape"})])
    result = await _call(server, "list_analysis_jobs", {"status": "completed"})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"
    assert result.is_error is False

    server, _ = _build([_http_200({"unexpected": "shape"})])
    result = await _call(server, "list_analysis_jobs", {"status": "failed"})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"
    assert result.is_error is False


async def test_list_non_object_item_degrades_to_bridge_internal() -> None:
    """R14: a non-object item must be rejected — the old ``_dict_items``
    silently DROPPED such rows, masking engine contract violations."""
    server, _ = _build(
        [_http_200({"items": ["oops"], "total": 1, "limit": 20, "offset": 0})]
    )
    result = await _call(server, "list_analysis_jobs", {"status": "completed"})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"
    assert result.is_error is False


async def test_wire_type_violations_degrade_to_bridge_internal() -> None:
    """R16 (review round 7): wrong-typed or REST-required-missing fields are
    contract violations — the bridge must never fix up engine data into a
    successful page."""
    # completed view: numeric job_id on an otherwise complete HistoryItem row
    history_row = {
        "job_id": 123,
        "analysis_id": "h1",
        "title": "T",
        "platform": "youtube",
        "author": "a",
        "video_id": "h1",
        "source_url": "https://youtu.be/h1",
        "published_at": None,
        "analyzed_at": 100.0,
        "language": "zh",
        "summary_mode": "auto",
        "asr_provider": "platform",
        "asr_model": "subtitles",
        "asr_model_version": "v1",
        "tags": [],
        "summary_preview": "…",
        "file_path": "h1.md",
        "content_hash": "0" * 64,
        "doc_status": "active",
        "indexed_at": 100.0,
    }
    server, _ = _build(
        [_http_200({"items": [history_row], "total": 1, "limit": 20, "offset": 0})]
    )
    result = await _call(server, "list_analysis_jobs", {"status": "completed"})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"
    assert result.is_error is False

    # failed view: boolean progress + list title + missing created_at
    bad_jobs_row = {"job_id": "x", "status": "failed", "progress": True}
    server, _ = _build(
        [_http_200({"items": [bad_jobs_row], "total": 1, "limit": 20, "offset": 0})]
    )
    result = await _call(server, "list_analysis_jobs", {"status": "failed"})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"

    bad_title_row = {
        "job_id": "x",
        "status": "failed",
        "progress": 0,
        "created_at": 1.0,
        "title": ["bad"],
    }
    server, _ = _build(
        [_http_200({"items": [bad_title_row], "total": 1, "limit": 20, "offset": 0})]
    )
    result = await _call(server, "list_analysis_jobs", {"status": "failed"})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"

    # unified job row missing the REST-required progress
    server, _ = _build(
        [
            _http_200(
                {
                    "items": [{"kind": "job", "job_id": "j0", "status": "queued"}],
                    "total": 1,
                    "jobs_total": 1,
                    "history_total": 0,
                    "limit": 1,
                    "offset": 0,
                }
            )
        ]
    )
    result = await _call(server, "list_analysis_jobs", {"limit": 1})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"


async def test_unified_reversed_segments_degrade_to_bridge_internal() -> None:
    """R17 (review round 7): totals may be perfectly consistent and every row
    individually valid — a page whose rows sit in the WRONG segment order
    still degrades (it would yield wrong pages, omissions, duplicates)."""
    reversed_page = {
        "items": [
            {
                "kind": "history",
                "job_id": "h1",
                "title": "T",
                "status": "completed",
                "progress": 100,
            },
            {"kind": "job", "job_id": "j1", "status": "failed", "progress": 0},
        ],
        "total": 2,
        "jobs_total": 1,
        "history_total": 1,
        "limit": 2,
        "offset": 0,
    }
    server, _ = _build([_http_200(reversed_page)])
    result = await _call(server, "list_analysis_jobs", {"limit": 2})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"
    assert result.is_error is False


async def test_unified_malformed_engine_body_degrades_to_bridge_internal() -> None:
    """R13 (review round 5, P2): a malformed 200 body from the unified list
    must degrade to a NORMAL tool result (ok:false / BRIDGE_INTERNAL,
    isError=false) — never masquerade as a successful empty page (which
    would silently hide every task and report) and never escape as a
    protocol-level ToolError."""
    server, _ = _build([_http_200({"unexpected": "shape"})])
    result = await _call(server, "list_analysis_jobs", {"limit": 5})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"
    assert result.is_error is False


async def test_unified_item_missing_status_degrades_to_bridge_internal() -> None:
    """R13: an item that fails per-kind validation (missing/invalid status)
    degrades the SAME way — the KeyError path must not bypass the envelope."""
    server, _ = _build(
        [_http_200({"items": [{"kind": "job", "job_id": "x"}], "total": 1})]
    )
    result = await _call(server, "list_analysis_jobs", {"limit": 5})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"
    assert result.is_error is False

    server, _ = _build(
        [_http_200({"items": [{"kind": "job", "job_id": "x", "status": "bogus"}], "total": 1})]
    )
    result = await _call(server, "list_analysis_jobs", {"limit": 5})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BRIDGE_INTERNAL"
    assert result.is_error is False
