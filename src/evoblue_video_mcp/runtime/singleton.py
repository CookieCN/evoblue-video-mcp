"""Single-instance guard for the Local Engine (INSTALLER_RELEASE_CONTRACT §3).

Three ordered checks at startup, any failure exits with a stable code and a
Chinese message (no Python traceback):

1. Windows mutex ``EvoBlueVideoMCP-Engine`` - a *plain, unprefixed* literal so
   the Inno Setup ``AppMutex`` directive (which auto-prefixes ``Local\`` under
   ``PrivilegesRequired=lowest``) can find it. Held for the process lifetime.
2. Lock file ``<data>/engine.lock`` carrying pid/port/started_at/version.
   Stale entries are stolen after a grace window; a live pid means another
   instance owns the data directory. The unlink+O_EXCL steal is deliberately
   not atomic - the mutex and the port bind are the authoritative checks (do
   not add a second lock).
3. A pre-bind probe of 127.0.0.1:<port>: turns address-in-use into exit
   code 4 with guidance instead of a uvicorn traceback. The real bind remains
   the final authority for the race window between probe and uvicorn.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import time
from pathlib import Path

EXIT_ALREADY_RUNNING = 3
EXIT_PORT_UNAVAILABLE = 4

MUTEX_NAME = "EvoBlueVideoMCP-Engine"
LOCK_FILENAME = "engine.lock"
LOCK_PAYLOAD_KEYS = ("pid", "port", "started_at", "version")

#: A lock file younger than this is never stolen (corrupt or dead-pid): the
#: other process may still be mid-startup (install/upgrade races).
_STEAL_GRACE_SECONDS = 5.0

_MSG_ALREADY_RUNNING = (
    "EvoBlue Engine 已在运行（端口 {port}）。请先退出当前实例，"
    "或直接使用已运行实例的 WebUI。"
)
_MSG_PORT_UNAVAILABLE = (
    "端口 {port} 已被其他程序占用，EvoBlue Engine 无法启动。"
    "可用环境变量 EVOBLUE_ENGINE_PORT 更换端口后重试。"
)


def _pid_alive(pid: int) -> bool:
    """Platform pid liveness without a psutil dependency."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        # getattr keeps the mypy pass platform-neutral (windll is win32-only);
        # B009 would prefer direct attribute access - not applicable here.
        kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return bool(code.value == STILL_ACTIVE)
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_mutex() -> object | None:
    """Create the session-local mutex, or None when one already exists.

    Windows only; other platforms return a dummy owner (the lock file and the
    port bind carry the single-instance guarantee there).
    """
    if sys.platform != "win32":
        return object()
    import ctypes

    ERROR_ALREADY_EXISTS = 183
    # See _pid_alive: getattr for platform-neutral typing (noqa B009).
    kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009
    handle: object = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def _lock_path(data_dir: Path) -> Path:
    return data_dir / LOCK_FILENAME


def _read_lock(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or any(key not in payload for key in LOCK_PAYLOAD_KEYS):
        return None
    return payload


def try_acquire_lock(data_dir: Path, *, port: int, version: str) -> int | None:
    """Create the lock file; return an exit code on conflict, fail-safe to 3.

    Ownership rules (contract §3): a parseable lock whose pid is alive means
    another instance is running. A stale or corrupt lock is stolen - unless it
    is younger than the grace window, which protects against stealing during
    another instance's startup (install/upgrade races).
    """
    path = _lock_path(data_dir)
    payload = json.dumps(
        {
            "pid": os.getpid(),
            "port": port,
            "started_at": time.time(),
            "version": version,
        }
    )
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = _read_lock(path)
            age = max(0.0, time.time() - path.stat().st_mtime)
            pid_value = existing.get("pid") if existing is not None else None
            live = isinstance(pid_value, int) and _pid_alive(pid_value)
            if live:
                return EXIT_ALREADY_RUNNING
            if age < _STEAL_GRACE_SECONDS:
                # Cannot prove staleness safely yet (corrupt or just-dead):
                # treat as a running instance during the startup window.
                return EXIT_ALREADY_RUNNING
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            return None


def release_lock(data_dir: Path) -> None:
    """Best-effort removal of our own lock file on clean shutdown."""
    try:
        path = _lock_path(data_dir)
        payload = _read_lock(path)
        if payload is not None and payload.get("pid") == os.getpid():
            path.unlink()
    except OSError:
        return


def port_available(host: str, port: int) -> bool:
    """Pre-bind probe: true when host:port can be bound right now."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def acquire(data_dir: Path | str, *, host: str, port: int, version: str) -> int | None:
    """Run the ordered startup checks; return an exit code or None to proceed."""
    data_dir = Path(data_dir)
    if acquire_mutex() is None:
        return EXIT_ALREADY_RUNNING
    if try_acquire_lock(
        data_dir, port=port, version=version
    ) is not None:
        return EXIT_ALREADY_RUNNING
    if not port_available(host, port):
        release_lock(data_dir)
        return EXIT_PORT_UNAVAILABLE
    return None


def conflict_message(code: int, port: int) -> str:
    """The frozen Chinese message for a conflict exit code."""
    if code == EXIT_ALREADY_RUNNING:
        return _MSG_ALREADY_RUNNING.format(port=port)
    if code == EXIT_PORT_UNAVAILABLE:
        return _MSG_PORT_UNAVAILABLE.format(port=port)
    raise ValueError(f"not a conflict exit code: {code}")
