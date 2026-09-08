"""P7-003 single-instance guard: contract §3 rules under controlled state."""

import json
import os
import socket
import time
from pathlib import Path

import pytest

from evoblue_video_mcp.runtime import singleton


def _free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_lock_payload_keys_and_name_are_frozen() -> None:
    assert singleton.LOCK_PAYLOAD_KEYS == ("pid", "port", "started_at", "version")
    assert singleton.LOCK_FILENAME == "engine.lock"
    assert singleton.MUTEX_NAME == "EvoBlueVideoMCP-Engine"
    assert singleton.EXIT_ALREADY_RUNNING == 3
    assert singleton.EXIT_PORT_UNAVAILABLE == 4


def test_acquire_creates_lock_and_second_attempt_conflicts(tmp_path: Path) -> None:
    port = _free_port()
    assert singleton.try_acquire_lock(tmp_path, port=port, version="1") is None
    lock = json.loads((tmp_path / "engine.lock").read_text(encoding="utf-8"))
    assert lock["pid"] == os.getpid()
    assert lock["port"] == port
    assert lock["version"] == "1"

    # a second process in THIS process still sees a live pid -> conflict 3
    assert singleton.try_acquire_lock(tmp_path, port=port, version="1") == 3


def test_stale_lock_is_stolen(tmp_path: Path) -> None:
    port = _free_port()
    stale = {"pid": 999999999, "port": port, "started_at": 0.0, "version": "0"}
    path = tmp_path / "engine.lock"
    path.write_text(json.dumps(stale), encoding="utf-8")
    old = time.time() - (singleton._STEAL_GRACE_SECONDS + 10)
    os.utime(path, (old, old))

    assert singleton.try_acquire_lock(tmp_path, port=port, version="1") is None
    lock = json.loads(path.read_text(encoding="utf-8"))
    assert lock["pid"] == os.getpid()


def test_corrupt_recent_lock_is_not_stolen(tmp_path: Path) -> None:
    path = tmp_path / "engine.lock"
    path.write_text("not json", encoding="utf-8")  # freshly written: mtime now
    assert singleton.try_acquire_lock(tmp_path, port=_free_port(), version="1") == 3


def test_release_lock_only_removes_own_lock(tmp_path: Path) -> None:
    path = tmp_path / "engine.lock"
    assert singleton.try_acquire_lock(tmp_path, port=_free_port(), version="1") is None
    singleton.release_lock(tmp_path)
    assert not path.exists()
    # someone else's lock stays
    foreign = {"pid": os.getpid() + 1_000_000, "port": 1, "started_at": 0.0, "version": "0"}
    path.write_text(json.dumps(foreign), encoding="utf-8")
    old = time.time() - (singleton._STEAL_GRACE_SECONDS + 10)
    os.utime(path, (old, old))
    singleton.release_lock(tmp_path)
    assert path.exists()


def test_port_available_reflects_bound_port() -> None:
    port = _free_port()
    assert singleton.port_available("127.0.0.1", port) is True
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        assert singleton.port_available("127.0.0.1", port) is False
    finally:
        blocker.close()


def test_conflict_messages_are_the_frozen_copy() -> None:
    msg3 = singleton.conflict_message(singleton.EXIT_ALREADY_RUNNING, 8765)
    msg4 = singleton.conflict_message(singleton.EXIT_PORT_UNAVAILABLE, 8765)
    assert "已在运行" in msg3 and "8765" in msg3
    assert "已被其他程序占用" in msg4 and "8765" in msg4
    with pytest.raises(ValueError):
        singleton.conflict_message(0, 8765)


def test_acquire_returns_port_conflict_for_busy_port(
    tmp_path: Path, monkeypatch
) -> None:
    # hermetic: a live EvoBlue engine elsewhere on the machine holds the real
    # mutex, which would (correctly) win with exit 3 before the port probe
    monkeypatch.setattr(singleton, "acquire_mutex", lambda: object())
    port = _free_port()
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        code = singleton.acquire(tmp_path, host="127.0.0.1", port=port, version="1")
        assert code == singleton.EXIT_PORT_UNAVAILABLE
        assert not (tmp_path / "engine.lock").exists(), "lock released on port failure"
    finally:
        blocker.close()
