"""P4-003: Bridge EngineClient + the frozen error translation matrix.

The matrix is the single place Engine HTTP shapes (legacy ``{"detail": ...}``,
P3 ``{"error": {...}}``, transport failures) become frozen MCP tool codes —
every row here is a contract row (docs/MCP_TOOLS.md §0.2, ADR 0004).
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.config import Settings
from evoblue_video_mcp.mcp.engine_client import (
    TIMEOUT_HEALTH_S,
    TIMEOUT_LIST_S,
    TIMEOUT_SUBMIT_S,
    EngineClient,
)
from evoblue_video_mcp.mcp.errors import translate_request_error, translate_status
from evoblue_video_mcp.mcp.schemas import ToolError
from evoblue_video_mcp.storage import build_engine, init_db


def _translated(status: int, body: object | None) -> ToolError:
    error = translate_status(status, body)
    assert error is not None
    return error


# --- transport failures ---


def test_connect_failures_map_to_engine_not_ready() -> None:
    for exc in (httpx.ConnectError("refused"), httpx.ConnectTimeout("slow")):
        error = translate_request_error(exc)
        assert error.code == "ENGINE_NOT_READY"
        assert error.retryable is True
        assert error.detail == type(exc).__name__


def test_budget_timeouts_map_to_engine_timeout() -> None:
    for exc in (
        httpx.ReadTimeout("late"),
        httpx.WriteTimeout("late"),
        httpx.PoolTimeout("late"),
    ):
        error = translate_request_error(exc)
        assert error.code == "ENGINE_TIMEOUT"
        assert error.retryable is True


def test_transport_errors_never_leak_exception_text() -> None:
    error = translate_request_error(httpx.ReadError("socket reset by 127.0.0.1:8765"))
    assert error.detail == "ReadError"
    assert "127.0.0.1" not in (error.detail or "")


# --- status translation: legacy {"detail": ...} shape ---


def test_two_hundred_is_success() -> None:
    assert translate_status(200, {"anything": True}) is None
    assert translate_status(204, None) is None


def test_unauthorized_maps_to_repairing_guidance() -> None:
    error = _translated(401, {"detail": "invalid local access token"})
    assert error.code == "ENGINE_UNAUTHORIZED"
    assert error.retryable is False
    assert "WebUI" in error.message


def test_job_not_found_legacy_detail() -> None:
    error = _translated(404, {"detail": "job not found"})
    assert error.code == "JOB_NOT_FOUND"
    assert error.retryable is False


def test_submit_validation_codes_pass_through() -> None:
    assert _translated(422, {"detail": "INVALID_URL"}).code == "INVALID_URL"
    assert (
        _translated(422, {"detail": "UNSUPPORTED_PLATFORM"}).code
        == "UNSUPPORTED_PLATFORM"
    )


# --- status translation: P3 {"error": {code, message}} shape ---


def test_p3_envelope_codes_pass_message_through() -> None:
    error = _translated(
        422, {"error": {"code": "QUERY_INVALID", "message": "q must be 1-500"}}
    )
    assert error.code == "QUERY_INVALID"
    assert error.message == "q must be 1-500"


def test_report_file_missing_becomes_report_not_ready() -> None:
    error = _translated(410, {"error": {"code": "REPORT_FILE_MISSING", "message": "gone"}})
    assert error.code == "REPORT_NOT_READY"
    assert error.retryable is True


def test_history_item_not_found_becomes_job_not_found() -> None:
    error = _translated(
        404, {"error": {"code": "HISTORY_ITEM_NOT_FOUND", "message": "missing"}}
    )
    assert error.code == "JOB_NOT_FOUND"


def test_search_index_unavailable_stays_retryable() -> None:
    error = _translated(
        503, {"error": {"code": "SEARCH_INDEX_UNAVAILABLE", "message": "down"}}
    )
    assert error.code == "SEARCH_INDEX_UNAVAILABLE"
    assert error.retryable is True


def test_unknown_engine_code_falls_back_to_protocol_error() -> None:
    error = _translated(
        409, {"error": {"code": "REBUILD_ALREADY_RUNNING", "message": "busy"}}
    )
    assert error.code == "ENGINE_PROTOCOL_ERROR"
    assert error.retryable is False
    assert error.detail == "HTTP 409"


def test_unrecognized_shapes_fall_back_with_status_detail() -> None:
    five_hundred = _translated(500, {"detail": "boom: C:\\secret\\path"})
    assert five_hundred.code == "ENGINE_PROTOCOL_ERROR"
    assert five_hundred.retryable is True
    assert five_hundred.detail == "HTTP 500"
    assert "secret" not in (five_hundred.detail or "")

    not_found = _translated(404, {"detail": "Not Found"})
    assert not_found.code == "ENGINE_PROTOCOL_ERROR"
    assert not_found.retryable is False

    empty = _translated(502, None)
    assert empty.code == "ENGINE_PROTOCOL_ERROR"
    assert empty.detail == "HTTP 502"


# --- EngineClient behavior ---


class RecordingHttp:
    """Minimal httpx.AsyncClient stand-in recording budget plumbing."""

    def __init__(self, response: httpx.Response | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def get(
        self,
        url: str,
        *,
        params: object = None,
        headers: object = None,
        timeout: object = None,
    ) -> httpx.Response:
        self.calls.append(
            {"method": "GET", "url": url, "params": params, "headers": headers, "timeout": timeout}
        )
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def post(
        self,
        url: str,
        *,
        json: object = None,
        headers: object = None,
        timeout: object = None,
    ) -> httpx.Response:
        self.calls.append(
            {"method": "POST", "url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


async def test_budget_constants_are_passed_per_request() -> None:
    recording = RecordingHttp(httpx.Response(200, json={}))
    client = EngineClient(recording, base_url="http://127.0.0.1:8765/", token="t")

    await client.get_json("/api/jobs", timeout_s=TIMEOUT_LIST_S)
    await client.post_json(
        "/api/jobs", payload={"url": "https://x"}, timeout_s=TIMEOUT_SUBMIT_S
    )
    assert recording.calls[0]["timeout"] == TIMEOUT_LIST_S
    assert recording.calls[1]["timeout"] == TIMEOUT_SUBMIT_S
    assert recording.calls[1]["json"] == {"url": "https://x"}


async def test_token_header_and_none_params_dropped() -> None:
    recording = RecordingHttp(httpx.Response(200, json={}))
    client = EngineClient(recording, base_url="http://127.0.0.1:8765", token="tok")

    await client.get_json("/api/jobs", params={"limit": 20, "status": None}, timeout_s=1.0)
    call = recording.calls[0]
    assert call["headers"] == {"X-Local-Token": "tok"}
    assert call["params"] == {"limit": "20"}  # None dropped, values stringified
    assert call["url"] == "http://127.0.0.1:8765/api/jobs"


async def test_non_json_body_parses_as_none() -> None:
    body = httpx.Response(502, text="<html>bad gateway</html>")
    client = EngineClient(RecordingHttp(body), base_url="http://x", token=None)
    status, parsed = await client.get_json("/api/health", timeout_s=1.0)
    assert status == 502
    assert parsed is None


async def test_health_returns_none_on_connect_failure() -> None:
    client = EngineClient(
        RecordingHttp(httpx.ConnectError("refused")), base_url="http://x", token=None
    )
    assert await client.health() is None


async def test_health_returns_body_on_success() -> None:
    client = EngineClient(
        RecordingHttp(httpx.Response(200, json={"status": "ok", "version": "1.0"})),
        base_url="http://x",
        token=None,
    )
    assert await client.health() == {"status": "ok", "version": "1.0"}


async def test_health_timeout_propagates_not_swallowed() -> None:
    client = EngineClient(
        RecordingHttp(httpx.ReadTimeout("late")), base_url="http://x", token=None
    )
    with pytest.raises(httpx.ReadTimeout):
        await client.health()


@pytest.fixture
async def engine_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine(tmp_path / "bridge.db")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def test_from_settings_builds_loopback_url_and_token_override(
    engine_factory: async_sessionmaker[AsyncSession],
) -> None:
    del engine_factory
    settings = Settings(engine_port=8901, local_access_token="env-token")
    client = EngineClient.from_settings(settings)
    try:
        assert client.base_url == "http://127.0.0.1:8901"
    finally:
        await client.aclose()

    recording = RecordingHttp(httpx.Response(200, json={}))
    override = EngineClient.from_settings(settings, token="file-token")
    override._http = recording
    await override.get_json("/api/health", timeout_s=TIMEOUT_HEALTH_S)
    assert recording.calls[0]["headers"] == {"X-Local-Token": "file-token"}
