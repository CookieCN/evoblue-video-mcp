"""P4-006: two independent MCP clients (separate Bridge processes) must not
duplicate work — the Engine's submit fingerprint reuses one job, and
concurrent cancels from both clients stay safe (IMMEDIATE serialization).
"""

import asyncio
import json
import os
import socket
import sys
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
from evoblue_video_mcp.runtime.bootstrap import create_runtime_app

_TOKEN = "multi-client-token"
_URL = "https://www.youtube.com/watch?v=multiclient"


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
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True, name="engine-uvicorn")
    thread.start()
    return _Engine(settings=settings, thread=thread, server=server)


def _wait_for_health(port: int, *, timeout_s: float = 25.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1.0).status_code == 200:
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
        }
    )
    return StdioServerParameters(
        command=sys.executable, args=["-m", "evoblue_video_mcp.mcp"], env=env
    )


def _payload(result: Any) -> dict[str, Any]:
    return json.loads(result.content[0].text)


async def test_two_clients_submitting_same_url_reuse_one_job(
    live_engine: _Engine,
) -> None:
    params = _bridge_params(live_engine.settings)
    async with (
        stdio_client(params) as (r1, w1),
        ClientSession(r1, w1, read_timeout_seconds=30.0) as client_a,
        stdio_client(params) as (r2, w2),
        ClientSession(r2, w2, read_timeout_seconds=30.0) as client_b,
    ):
        await client_a.initialize()
        await client_b.initialize()

        first = await client_a.call_tool("submit_video_analysis", {"url": _URL})
        second = await client_b.call_tool("submit_video_analysis", {"url": _URL})
        p1, p2 = _payload(first), _payload(second)
        assert p1["ok"] is True and p1["reused"] is False
        assert p2["ok"] is True and p2["reused"] is True
        assert p1["job_id"] == p2["job_id"]


async def test_concurrent_cancels_from_two_clients_stay_safe(
    live_engine: _Engine,
) -> None:
    params = _bridge_params(live_engine.settings)
    async with (
        stdio_client(params) as (r1, w1),
        ClientSession(r1, w1, read_timeout_seconds=30.0) as client_a,
        stdio_client(params) as (r2, w2),
        ClientSession(r2, w2, read_timeout_seconds=30.0) as client_b,
    ):
        await client_a.initialize()
        await client_b.initialize()

        submitted = await client_a.call_tool(
            "submit_video_analysis", {"url": _URL}
        )
        job_id = _payload(submitted)["job_id"]

        first, second = await asyncio.gather(
            client_a.call_tool("cancel_analysis", {"job_id": job_id}),
            client_b.call_tool("cancel_analysis", {"job_id": job_id}),
        )
        p_first, p_second = _payload(first), _payload(second)
        # Safety invariants independent of worker timing: neither cancel may
        # produce a protocol error, at most one may claim the effect (the
        # test Engine's worker can terminalize the job first — no LLM creds —
        # in which case both correctly answer accepted=false), and the job
        # must end terminal.
        assert p_first["ok"] is True and p_second["ok"] is True
        assert [p_first["accepted"], p_second["accepted"]].count(True) <= 1

        final = await client_b.call_tool("get_analysis_status", {"job_id": job_id})
        assert _payload(final)["status"] in {"cancelled", "failed"}
