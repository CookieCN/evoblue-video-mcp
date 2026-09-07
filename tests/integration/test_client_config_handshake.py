"""P5-002 integration: the handshake kernel and the P4 script against a live
Engine (threaded uvicorn) — the same wire contracts test_bridge_stdio.py
pins, but through the shared kernel that the WebUI verify flow will call.

Covers: success predicate (name + exact tool set), script delegation exit 0,
and the documented property that a handshake can succeed while the Engine is
offline (the Bridge registers tools statically; engine liveness is a separate
probe — contract §6).
"""

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn

from evoblue_video_mcp.application.handshake import verify_bridge_handshake
from evoblue_video_mcp.config import Settings
from evoblue_video_mcp.mcp.schemas import TOOL_NAMES
from evoblue_video_mcp.runtime.bootstrap import create_runtime_app

_TOKEN = "p5-handshake-token"
_ROOT = Path(__file__).resolve().parents[2]


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


def _kernel_env(port: int | None) -> dict[str, str]:
    env = os.environ.copy()
    if port is not None:
        env["EVOBLUE_ENGINE_PORT"] = str(port)
    env["EVOBLUE_LOCAL_TOKEN"] = _TOKEN
    env.setdefault("PYTHONUTF8", "1")
    return env


async def test_kernel_success_predicate_over_the_wire(live_engine: _Engine) -> None:
    result = await verify_bridge_handshake(
        sys.executable,
        ["-m", "evoblue_video_mcp.mcp"],
        _kernel_env(live_engine.settings.engine_port),
    )
    assert result.ok is True, result
    assert result.error == "ok"
    assert result.server_name == "evoblue-video"
    assert result.protocol_version is not None
    assert result.tool_names == tuple(sorted(TOOL_NAMES))
    assert result.elapsed_s > 0.0


async def test_handshake_succeeds_even_with_engine_offline() -> None:
    """Documented property: tools register statically, so a healthy Bridge
    handshake does not require a running Engine (contract §6 note)."""
    dead_port = _free_port()
    result = await verify_bridge_handshake(
        sys.executable,
        ["-m", "evoblue_video_mcp.mcp"],
        _kernel_env(dead_port),
    )
    assert result.ok is True, result
    assert result.tool_names == tuple(sorted(TOOL_NAMES))


def test_script_delegation_check_handshake_exit_zero(live_engine: _Engine) -> None:
    """The P4 CLI now delegates to the kernel and must stay a working path."""
    proc = subprocess.run(
        [
            sys.executable,
            str(_ROOT / "scripts" / "verify_p4_clients.py"),
            "check-handshake",
            "--port",
            str(live_engine.settings.engine_port),
        ],
        env=_kernel_env(live_engine.settings.engine_port),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "HANDSHAKE OK" in proc.stdout
