"""Claude Code adapter — the official CLI is the only write surface.

``claude mcp add/get/remove`` (local scope) is the P4-measured path; the CLI
manages its own storage and we never touch its files, so this tier has no
file backups — reinstalling the same payload IS the restore path (contract
§5). Verification semantics are strict: only a zero-exit ``claude mcp get``
whose output clearly contains our command counts as configuration evidence;
anything else — CLI missing, unparsable output, timeout — is 「未验证」 with
a machine-readable reason, never a guessed success.
"""

import asyncio
import shutil
from collections.abc import Sequence
from dataclasses import dataclass

from evoblue_video_mcp.application.client_config.adapters.base import (
    HandshakeVerifier,
    OperationOutcome,
    TargetState,
)
from evoblue_video_mcp.application.client_config.models import (
    BackupInfo,
    ClientSpec,
    EntryPayload,
)

_CLI_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class CliResult:
    returncode: int | None  # None = timed out
    stdout: str
    stderr: str


class CliRunner:
    """Production runner: resolve ``claude`` on PATH and spawn it."""

    def __init__(self, *, program: str = "claude", timeout_s: float = _CLI_TIMEOUT_S) -> None:
        self._program = program
        self._timeout_s = timeout_s

    async def run(self, args: Sequence[str]) -> CliResult:
        resolved = shutil.which(self._program)
        if resolved is None:
            raise CliUnavailableError(f"cli not on PATH: {self._program}")
        proc = await asyncio.create_subprocess_exec(
            resolved,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), self._timeout_s)
        except TimeoutError:
            proc.kill()
            return CliResult(returncode=None, stdout="", stderr="cli timed out")
        return CliResult(
            returncode=proc.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )


class CliUnavailableError(Exception):
    pass


def _parse_get_output(stdout: str) -> tuple[str | None, list[str] | None, bool | None] | None:
    """Parse ``claude mcp get`` output into (command, args tokens, local scope).

    Returns ``None`` when no Command line exists at all (unparseable output).
    Individual fields stay ``None`` when their line is missing — the caller
    must treat every unconfirmed field as unverifiable, never as a match.
    """
    command: str | None = None
    args_tokens: list[str] | None = None
    scope_local: bool | None = None
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered.startswith("command:"):
            command = line.split(":", 1)[1].strip()
        elif lowered.startswith("args:"):
            raw_args = line.split(":", 1)[1].strip()
            args_tokens = raw_args.split() if raw_args else []
        elif lowered.startswith("scope:"):
            scope_local = "local" in lowered
    if command is None:
        return None
    return command, args_tokens, scope_local


class ClaudeCodeAdapter:
    def __init__(
        self,
        spec: ClientSpec,
        *,
        verifier: HandshakeVerifier,
        runner: CliRunner,
    ) -> None:
        self._spec = spec
        self._verifier = verifier
        self._runner = runner

    def _add_args(self, payload: EntryPayload) -> list[str]:
        env_args: list[str] = []
        for key, value in payload.env:
            env_args += ["-e", f"{key}={value}"]
        return [
            "mcp",
            "add",
            self._spec.entry_key,
            "--scope",
            "local",
            *env_args,
            "--",
            payload.command,
            *payload.args,
        ]

    async def install(self, payload: EntryPayload, *, force: bool) -> OperationOutcome:
        del force  # the CLI replaces the entry itself; no unmanaged-key gate here
        try:
            result = await self._runner.run(self._add_args(payload))
        except CliUnavailableError:
            return OperationOutcome(performed=False, reason="claude_cli_not_found")
        if result.returncode != 0:
            return OperationOutcome(
                performed=False, reason="cli_add_failed", installed=None
            )
        confirmed = await self.verify(payload)
        return OperationOutcome(
            performed=True,
            reason=confirmed.reason,  # None when the CLI get confirms the entry
            installed=confirmed.installed,
            entry_matches_current=confirmed.entry_matches_current,
        )

    async def verify(self, payload: EntryPayload) -> OperationOutcome:
        """Configuration evidence + real handshake — both required for verified.

        Evidence only counts when the CLI output confirms command AND args AND
        local scope. Any field that cannot be confirmed leaves the entry
        unverifiable — an entry carrying the right Python but the wrong module
        or scope must never read as verified.
        """
        try:
            # `claude mcp get` takes no scope flag (measured 2026-09-07, CLI
            # v2.x): it resolves the name across scopes and reports which one
            # matched. Windows path comparison must be case-insensitive.
            result = await self._runner.run(["mcp", "get", self._spec.entry_key])
        except CliUnavailableError:
            return OperationOutcome(
                performed=False, reason="claude_cli_not_found", installed=None
            )
        if result.returncode != 0:
            return OperationOutcome(
                performed=False, reason="not_installed", installed=False
            )
        parsed = _parse_get_output(result.stdout)
        if parsed is None:
            return OperationOutcome(
                performed=False,
                reason="cli_output_unparsable",
                installed=True,
                entry_matches_current=None,
            )
        if payload.env:
            # `claude mcp get` does not surface env vars — evidence for a
            # non-default-port entry cannot be established from the CLI.
            return OperationOutcome(
                performed=False,
                reason="cli_env_unverifiable",
                installed=True,
                entry_matches_current=None,
            )
        command, args_tokens, scope_local = parsed
        if (
            command is not None
            and args_tokens is not None
            and scope_local is not None
            and command.lower() == payload.command.lower()
            and args_tokens == list(payload.args)
            and scope_local
        ):
            return OperationOutcome(
                performed=False, installed=True, entry_matches_current=True
            )
        mismatch = command is not None and args_tokens is not None
        return OperationOutcome(
            performed=False,
            reason="entry_mismatch" if mismatch else "cli_output_unparsable",
            installed=True,
            entry_matches_current=False if mismatch else None,
        )

    async def remove(self, payload: EntryPayload) -> OperationOutcome:
        del payload
        try:
            result = await self._runner.run(
                ["mcp", "remove", self._spec.entry_key, "--scope", "local"]
            )
        except CliUnavailableError:
            return OperationOutcome(performed=False, reason="claude_cli_not_found")
        if result.returncode == 0:
            return OperationOutcome(performed=True, installed=False, removed_target=True)
        lowered = (result.stdout + result.stderr).lower()
        if "not found" in lowered or "no entry" in lowered:
            return OperationOutcome(
                performed=False, reason="not_installed", installed=False
            )
        return OperationOutcome(performed=False, reason="cli_remove_failed")

    async def restore(self, payload: EntryPayload, backup_name: str) -> OperationOutcome:
        del backup_name  # CLI tier keeps no file backups; reinstall = restore
        return await self.install(payload, force=False)

    async def state(self, payload: EntryPayload) -> TargetState:
        outcome = await self.verify(payload)
        return TargetState(
            supported=True,
            present=outcome.installed,
            installed=outcome.installed,
            entry_matches_current=outcome.entry_matches_current,
            other_server_count=None,
            backup_count=0,
        )

    async def backups(self) -> list[BackupInfo]:
        return []  # CLI-owned storage is never touched, so no file backups

    def copyable_text(self, payload: EntryPayload) -> str:
        from subprocess import list2cmdline

        head = [
            "mcp",
            "add",
            self._spec.entry_key,
            "--scope",
            "local",
        ]
        for key, value in payload.env:
            head += ["-e", f"{key}={value}"]
        head.append("--")
        return "claude " + " ".join(head) + " " + list2cmdline([payload.command, *payload.args])

    def copyable_steps(self) -> tuple[str, ...]:
        return ("在终端运行以下命令（claude CLI 在 PATH 中时）。",)
