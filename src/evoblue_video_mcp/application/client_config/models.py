"""Frozen value types for the client-config write surface.

Authority: docs/CLIENT_CONFIG_WRITE_CONTRACT.md §1 (client registry) and §6
(status enum + display labels). The contract drift test keeps the doc tables
and these types in lockstep — fix the contract first, then mirror it back.
"""

from dataclasses import dataclass, field
from typing import Literal

Tier = Literal["file_auto", "cli", "manual"]
Container = Literal["toml", "json", "cli"]
HandshakeState = Literal["verified", "unverified", "failed"]

#: Frozen display labels (contract §6). The UI may render these and only
#: these for a handshake verdict; 「未验证」 is the wording locked in tests.
HANDSHAKE_LABELS: dict[str, str] = {
    "verified": "已验证",
    "unverified": "未验证",
    "failed": "验证失败",
}


@dataclass(frozen=True)
class ClientSpec:
    """One row of the frozen client registry (contract §1).

    ``write_target`` is the doc-table cell verbatim (``~``/``%APPDATA%``
    resolved by ``paths.resolve_target``); ``None`` marks the manual tier,
    which has no auto path at all.
    """

    client_id: str
    display_name: str
    tier: Tier
    entry_key: str
    container: Container
    write_target: str | None
    note: str


@dataclass(frozen=True)
class EntryPayload:
    """The entry we manage inside a client's registry (contract §3).

    ``env`` is a tuple of pairs (frozen boundary object, per experience #7);
    empty in the default posture — a port override is the only key we ever
    write, and tokens are never written into client files.
    """

    command: str
    args: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class BackupInfo:
    """One backup file of one target (contract §5)."""

    name: str
    created_at: float
    size_bytes: int
    sha256: str
    was_absent: bool
    matches_current: bool | None = field(default=None)
