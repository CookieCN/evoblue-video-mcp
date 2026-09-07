"""Async MCP handshake kernel (docs/CLIENT_CONFIG_WRITE_CONTRACT.md §6).

Launches the configured entry exactly like a client would and performs a real
MCP ``initialize`` + ``list_tools``. Success = the server announces the
expected name *and* registers exactly ``TOOL_NAMES`` — writing a config file
successfully is never a verdict (contract §6: 写入文件成功本身永远不构成成功状态).

The whole probe is capped by ``asyncio.wait_for`` so a stalled child can never
hang the caller; cancellation unwinds the stdio contexts and terminates the
child, so nothing leaks (pyproject escalates leaked-thread warnings to errors).
stderr is captured to a file, redacted (paths shrink to their tail,
credential-shaped assignments are masked) and capped — never echoed in full
(experience #19). Note for callers: a handshake can succeed while the Engine
is offline, because the Bridge registers tools statically; engine liveness is
a separate probe.
"""

import asyncio
import re
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Final, Literal, TextIO, cast

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from evoblue_video_mcp.application.diagnostics import redact_path
from evoblue_video_mcp.mcp.schemas import TOOL_NAMES

HandshakeErrorCode = Literal[
    "ok",
    "spawn_failed",
    "timeout",
    "protocol_error",
    "name_mismatch",
    "tool_mismatch",
]

_STDIO_READ_TIMEOUT_S: Final = 30.0
_STDERR_TAIL_CHARS: Final = 2000
_CREDENTIAL_PATTERN: Final = re.compile(
    r"(?i)(?<![A-Za-z])(token|password|api[_-]?key|secret)(?![A-Za-z])[=:]\s*\S+"
)
#: Windows drive/UNC paths and POSIX absolute paths, best effort — the tail is
#: debug output, not a security boundary; the boundary is never echoing the
#: whole file (contract §8).
_PATH_PATTERN: Final = re.compile(
    r"[A-Za-z]:[\\/][^\s\"',;)\]]+|(?<![\w./-])/(?:[^\s\"'/]+/)+[^\s\"'/]+"
)


@dataclass(frozen=True)
class HandshakeResult:
    ok: bool
    error: HandshakeErrorCode
    server_name: str | None
    protocol_version: str | None
    tool_names: tuple[str, ...]
    stderr_tail: str | None
    elapsed_s: float


def redact_stderr(text: str | None, *, tail_chars: int = _STDERR_TAIL_CHARS) -> str | None:
    """Mask credential assignments, shrink paths, cap the tail."""
    if not text or not text.strip():
        return None
    text = _CREDENTIAL_PATTERN.sub("<redacted>", text)
    text = _PATH_PATTERN.sub(lambda match: redact_path(match.group(0)) or "<path>", text)
    text = text.strip()
    if not text:
        return None
    if len(text) > tail_chars:
        text = "…" + text[-tail_chars:]
    return text


def _stderr_tail(errlog: IO[str], *extra: str) -> str | None:
    parts = []
    try:
        errlog.seek(0)
        content = errlog.read()
    except (OSError, ValueError):
        content = ""
    for piece in (content, *extra):
        if piece:
            parts.append(piece)
    if not parts:
        return None
    return redact_stderr("\n".join(parts))


async def verify_bridge_handshake(
    command: str,
    args: Sequence[str],
    env: Mapping[str, str] | None,
    *,
    server_name: str = "evoblue-video",
    timeout_s: float = 45.0,
) -> HandshakeResult:
    """Run the real handshake against the configured entry.

    ``env`` is the complete child environment (process environment plus entry
    env), mirroring what a real client does; ``None`` lets the SDK build its
    default environment. The 45s default is the contract §6 total budget; the
    session-level read timeout is the inner guard.
    """
    started = time.monotonic()

    def _finish(
        *,
        ok: bool = False,
        error: HandshakeErrorCode = "ok",
        announced: str | None = None,
        protocol_version: str | None = None,
        tool_names: tuple[str, ...] = (),
        stderr_tail: str | None = None,
    ) -> HandshakeResult:
        return HandshakeResult(
            ok=ok,
            error=error,
            server_name=announced,
            protocol_version=protocol_version,
            tool_names=tool_names,
            stderr_tail=stderr_tail,
            elapsed_s=round(time.monotonic() - started, 3),
        )

    params = StdioServerParameters(
        command=command, args=list(args), env=dict(env) if env is not None else None
    )

    async def _handshake() -> tuple[str, str | None, tuple[str, ...]]:
        async with (
            stdio_client(params, errlog=cast("TextIO", errlog)) as (read, write),
            ClientSession(read, write, read_timeout_seconds=_STDIO_READ_TIMEOUT_S) as session,
        ):
            init = await session.initialize()
            listed = await session.list_tools()
            names = tuple(sorted(tool.name for tool in listed.tools))
            return init.server_info.name, init.protocol_version, names

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        try:
            announced, protocol_version, names = await asyncio.wait_for(
                _handshake(), timeout_s
            )
        except TimeoutError:
            return _finish(error="timeout", stderr_tail=_stderr_tail(errlog))
        except OSError as exc:
            return _finish(
                error="spawn_failed", stderr_tail=_stderr_tail(errlog, str(exc))
            )
        except Exception as exc:  # any SDK/protocol failure is itself the verdict
            return _finish(
                error="protocol_error", stderr_tail=_stderr_tail(errlog, str(exc))
            )
        stderr_tail = _stderr_tail(errlog)
        if announced != server_name:
            return _finish(
                error="name_mismatch",
                announced=announced,
                protocol_version=protocol_version,
                tool_names=names,
                stderr_tail=stderr_tail,
            )
        if names != tuple(sorted(TOOL_NAMES)):
            return _finish(
                error="tool_mismatch",
                announced=announced,
                protocol_version=protocol_version,
                tool_names=names,
                stderr_tail=stderr_tail,
            )
        return _finish(
            ok=True,
            announced=announced,
            protocol_version=protocol_version,
            tool_names=names,
            stderr_tail=stderr_tail,
        )
