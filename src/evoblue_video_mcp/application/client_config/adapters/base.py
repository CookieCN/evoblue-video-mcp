"""Adapter contract for per-client config operations (contract §6 gates).

Adapters are tier-shaped, not client-shaped: one :class:`FileAutoAdapter`
serves every file-based client (the differences are data in ``specs``), one
adapter drives the Claude Code CLI, and the manual tier refuses mutations.
Every adapter receives an injected handshake verifier — adapters never
construct processes themselves except through their own tier's mechanism
(CLI subprocess), and file adapters never spawn anything.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from evoblue_video_mcp.application.client_config.models import (
    BackupInfo,
    EntryPayload,
)
from evoblue_video_mcp.application.handshake import HandshakeResult

#: Runs the real MCP handshake against the payload's entry (the kernel),
#: recording the verdict in the service's cache. Injectable so tests can
#: script success/failure without spawning anything.
HandshakeVerifier = Callable[[EntryPayload], Awaitable[HandshakeResult]]


@dataclass(frozen=True)
class OperationOutcome:
    """What one operation actually did — the API layer only re-shapes this.

    ``performed=False`` means nothing was written (idempotent no-op, client
    directory missing, CLI unavailable, ...) — ``reason`` says why in a
    machine-readable token.
    """

    performed: bool
    reason: str | None = None
    installed: bool | None = None
    entry_matches_current: bool | None = None
    backup_name: str | None = None
    restored_from: str | None = None
    safety_backup: str | None = None
    removed_target: bool | None = None


@dataclass(frozen=True)
class TargetState:
    """Field-level read of one client target — never file contents (§8)."""

    supported: bool  # False: path unresolvable on this machine (POSIX appdata)
    present: bool | None  # target file exists (None: unsupported)
    installed: bool | None  # our entry present (None: unparseable/unsupported)
    entry_matches_current: bool | None
    other_server_count: int | None
    backup_count: int


class ClientAdapter(Protocol):
    async def install(self, payload: EntryPayload, *, force: bool) -> OperationOutcome: ...

    async def verify(self, payload: EntryPayload) -> OperationOutcome: ...

    async def remove(self, payload: EntryPayload) -> OperationOutcome: ...

    async def restore(self, payload: EntryPayload, backup_name: str) -> OperationOutcome: ...

    async def state(self, payload: EntryPayload) -> TargetState: ...

    async def backups(self) -> list[BackupInfo]: ...

    def copyable_text(self, payload: EntryPayload) -> str: ...

    def copyable_steps(self) -> tuple[str, ...]: ...
