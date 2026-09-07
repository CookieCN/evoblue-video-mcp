"""P4-005: real stdio handshake — real Engine (threaded uvicorn) + real Bridge
subprocess + MCP ClientSession.

Proves the frozen contracts over the wire: initialize handshake, seven tools
registered, flat ``ok`` envelopes in structured output, stdout purity (any
non-protocol byte on stdio breaks the session), stderr-only logging, and
ENGINE_NOT_READY when the Engine is offline (P4 scope: report + guide, never
auto-start).
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from evoblue_video_mcp.config import Settings
from evoblue_video_mcp.mcp import __main__ as bridge_main
from evoblue_video_mcp.mcp.schemas import TOOL_NAMES
from evoblue_video_mcp.runtime.bootstrap import create_runtime_app

_TOKEN = "stdio-e2e-token"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class _Engine:
    settings: Settings
    thread: threading.Thread
    server: uvicorn.Server


def _start_engine(data_dir: Path, port: int) -> _Engine:
    settings = Settings(
        environment="production",
        local_access_token=_TOKEN,
        engine_port=port,
        data_directory=data_dir,
    )
    app = create_runtime_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="engine-uvicorn")
    thread.start()
    return _Engine(settings=settings, thread=thread, server=server)


def _wait_for_health(port: int, *, timeout_s: float = 25.0) -> None:
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise RuntimeError("engine never became healthy")


@pytest.fixture
def live_engine(tmp_path: Path) -> Iterator[_Engine]:
    engine = _start_engine(tmp_path / "engine-data", _free_port())
    try:
        _wait_for_health(engine.settings.engine_port)
        yield engine
    finally:
        engine.server.should_exit = True
        engine.thread.join(timeout=20)
        assert not engine.thread.is_alive(), "uvicorn thread leaked past shutdown"


def _bridge_params(settings: Settings) -> StdioServerParameters:
    env = os.environ.copy()
    env.update(
        {
            "EVOBLUE_ENGINE_PORT": str(settings.engine_port),
            "EVOBLUE_LOCAL_TOKEN": _TOKEN,
            "EVOBLUE_DATA_DIRECTORY": str(settings.data_directory),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "evoblue_video_mcp.mcp"],
        env=env,
    )


def _struct(result: Any) -> dict[str, Any]:
    """Parse the flat envelope from a tool result's text content."""
    texts = [block.text for block in result.content]
    for text in texts:
        stripped = text.strip()
        if stripped.startswith("{"):
            return json.loads(stripped)
    raise AssertionError("no JSON text content in tool result")


async def test_discover_local_token_order() -> None:
    env_token = bridge_main.discover_local_token(
        Settings(local_access_token="from-env"), Path("unused")
    )
    assert env_token == "from-env"
    missing_dir = Path(__file__).parent / "_no_such_token_dir"
    assert bridge_main.discover_local_token(Settings(), missing_dir) is None


async def test_real_stdio_handshake_seven_tools_and_flat_envelope(
    live_engine: _Engine,
) -> None:
    params = _bridge_params(live_engine.settings)
    # stdio_client redirects the child's stderr into a real file handle.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        async with (
            stdio_client(params, errlog=errlog) as (read, write),
            ClientSession(read, write, read_timeout_seconds=30.0) as session,
        ):
            init = await session.initialize()
            assert init.server_info.name == "evoblue-video"
            listed = await session.list_tools()
            # Newer SDK returns a (page, cursor) tuple; older a plain list.
            tool_list = listed[0] if isinstance(listed, tuple) else listed
            tools = tool_list.tools if hasattr(tool_list, "tools") else tool_list
            assert sorted(tool.name for tool in tools) == sorted(TOOL_NAMES)

            listed = await session.call_tool("list_analysis_jobs", {"limit": 5})
            assert listed.is_error is False
            payload = _struct(listed)
            assert payload["ok"] is True
            assert payload["limit"] == 5

            diag = await session.call_tool("diagnose_environment", {})
            assert diag.is_error is False
            body = _struct(diag)
            assert body["ok"] is True
            assert body["redacted"] is True
            assert any(
                isinstance(check, dict)
                and check.get("name") == "local_engine"
                and check.get("status") == "pass"
                for check in body["checks"]
            )

        errlog.seek(0)
        bridge_logs = errlog.read()
    assert "starting bridge" in bridge_logs
    assert "Traceback" not in bridge_logs


async def test_offline_engine_reports_not_ready_over_the_wire(
    live_engine: _Engine,
) -> None:
    settings = Settings(
        environment="production",
        local_access_token=_TOKEN,
        engine_port=_free_port(),
        data_directory=live_engine.settings.data_directory,
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        async with (
            stdio_client(_bridge_params(settings), errlog=errlog) as (read, write),
            ClientSession(read, write, read_timeout_seconds=30.0) as session,
        ):
            await session.initialize()
            result = await session.call_tool("get_analysis_status", {"job_id": "w"})
            assert result.is_error is False
            body = _struct(result)
            assert body["ok"] is False
            assert body["error"]["code"] == "ENGINE_NOT_READY"
            assert body["error"]["retryable"] is True


async def test_token_file_discovery_path(live_engine: _Engine) -> None:
    data_dir = live_engine.settings.data_directory
    assert isinstance(data_dir, Path)
    (data_dir / "local_token").write_text(_TOKEN, encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "EVOBLUE_ENGINE_PORT": str(live_engine.settings.engine_port),
            "EVOBLUE_DATA_DIRECTORY": str(data_dir),
            "PYTHONUTF8": "1",
        }
    )
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "evoblue_video_mcp.mcp"], env=env
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        async with (
            stdio_client(params, errlog=errlog) as (read, write),
            ClientSession(read, write, read_timeout_seconds=30.0) as session,
        ):
            await session.initialize()
            listed = await session.call_tool("list_analysis_jobs", {})
            assert _struct(listed)["ok"] is True


def test_documented_bridge_launch_shape() -> None:
    params = _bridge_params(
        Settings(engine_port=8765, local_access_token="t", data_directory=Path("d"))
    )
    assert params.command == sys.executable
    assert params.args == ["-m", "evoblue_video_mcp.mcp"]
