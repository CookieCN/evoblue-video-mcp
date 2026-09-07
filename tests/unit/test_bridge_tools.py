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
    jobs = {"items": [{"job_id": "j1", "status": "queued", "progress": 0}], "total": 1}
    history = {
        "items": [{"job_id": "h1", "title": "T", "platform": "youtube"}],
        "total": 1,
    }
    server, http = _build([_http_200(jobs), _http_200(history)])
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
