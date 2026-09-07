"""Single write channel for the client-config surface (contract §4, ADR 0005).

Every byte this package puts on disk — and every file it removes — goes
through this module; the structural gate
(``tests/contract/test_client_write_guards.py``) confines write primitives to
it. The idiom mirrors ``storage/rebuild.py``: same-directory temp name,
fsync, ``os.replace``, temp cleanup on any failure.

The caller gets a :class:`WriteResult` carrying the sha256 of what the file
contains *as read back from disk* — never from the payload we sent. A write
that cannot be verified is a failed write (``CONFIG_WRITE_FAILED``), never a
success (contract §6: 写入文件成功本身永远不构成成功状态).
"""

import hashlib
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from evoblue_video_mcp.application.client_config.errors import ConfigWriteError


@dataclass(frozen=True)
class WriteResult:
    path: Path
    size_bytes: int
    sha256: str


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def commit_atomic(target: Path, payload: bytes) -> WriteResult:
    """Atomically replace ``target`` with ``payload`` and verify by reading back.

    The parent directory must already exist — creating it is forbidden
    (contract §2: 拒绝 mkdir, 父目录缺失即客户端未安装的实测信号).
    """
    if not target.parent.is_dir():
        raise ConfigWriteError(
            "target directory does not exist; refusing to create it"
        )
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{time.monotonic_ns()}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    written = target.read_bytes()
    if written != payload:
        raise ConfigWriteError(
            "post-write read-back mismatch; the write did not stick"
        )
    return WriteResult(path=target, size_bytes=len(written), sha256=sha256_hex(written))


def remove_atomic(target: Path) -> None:
    """Remove ``target`` and verify absence afterwards (idempotent)."""
    try:
        target.unlink()
    except FileNotFoundError:
        return
    if target.exists():
        raise ConfigWriteError("target still present after removal")


def remove_file(path: Path) -> None:
    """Best-effort removal for retention pruning (absent is fine)."""
    with suppress(OSError):
        path.unlink()
