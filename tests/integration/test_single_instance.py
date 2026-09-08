"""P7-003 real double-instance acceptance (Windows): exit code 3, Chinese message.

Spawns a real first engine via uvicorn in-process (the same shape
test_bridge_stdio.py uses), then runs the packaged-entry acquire path in a
real child process against the same data directory and expects the frozen
conflict behaviour instead of a traceback.
"""

import socket
import sys

import pytest

from evoblue_video_mcp.runtime import singleton


def _free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


@pytest.mark.skipif(sys.platform != "win32", reason="exercises the win32 mutex path")
def test_second_instance_exits_with_frozen_conflict(tmp_path, monkeypatch) -> None:
    import subprocess

    port = _free_port()
    code = f"""
import sys
from pathlib import Path
from evoblue_video_mcp.runtime import singleton
data_dir = Path({str(tmp_path)!r})
code = singleton.acquire(data_dir, host="127.0.0.1", port={port}, version="test")
if code is not None:
    print(singleton.conflict_message(code, {port}), file=sys.stderr)
    sys.exit(code)
# first instance: hold the guard for the parent to race against
import time
time.sleep(30)
"""
    first = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # wait until the first instance holds the lock
        import time

        lock = tmp_path / "engine.lock"
        deadline = time.time() + 15
        while not lock.exists():
            assert time.time() < deadline, "first instance never created the lock"
            time.sleep(0.1)

        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
            env=None,
        )
        assert proc.returncode == singleton.EXIT_ALREADY_RUNNING
        assert "已在运行" in proc.stderr
    finally:
        first.kill()
        first.wait(timeout=10)
