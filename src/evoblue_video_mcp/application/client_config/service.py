"""Client-config service — the orchestrator behind ``/api/mcp-clients``.

Owns: the per-client mutation lock (双开页并发), the in-memory handshake
verdict cache (contract §7: a verdict is a statement about *now*; Engine
restarts reset it by design), the frozen entry payload (§3: no tokens, env
only for a non-default port), and the mapping from adapter outcomes to API
shapes. Confirm gates are enforced here BEFORE any side effect (§5).
"""

import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from evoblue_video_mcp.application.client_config.adapters.base import (
    ClientAdapter,
    HandshakeVerifier,
    OperationOutcome,
    TargetState,
)
from evoblue_video_mcp.application.client_config.adapters.claude_code import (
    ClaudeCodeAdapter,
    CliRunner,
)
from evoblue_video_mcp.application.client_config.adapters.deepseek import DeepSeekAdapter
from evoblue_video_mcp.application.client_config.adapters.file_auto import FileAutoAdapter
from evoblue_video_mcp.application.client_config.errors import (
    AutoInstallUnsupportedError,
    ClientUnknownError,
    ConfirmationRequiredError,
    OperationInProgressError,
)
from evoblue_video_mcp.application.client_config.models import (
    BackupInfo,
    ClientSpec,
    Container,
    EntryPayload,
    HandshakeState,
    Tier,
)
from evoblue_video_mcp.application.client_config.specs import CLIENT_SPECS
from evoblue_video_mcp.application.handshake import (
    HandshakeResult,
    verify_bridge_handshake,
)

_HANDSHAKE_TIMEOUT_S = 45.0
_DEFAULT_PORT = 8765




@dataclass(frozen=True)
class CopyableConfig:
    client_id: str
    format: Container
    config_text: str
    target_path: str | None
    steps: tuple[str, ...]


@dataclass(frozen=True)
class ClientStatus:
    client_id: str
    display_name: str
    tier: Tier
    target_present: bool | None
    installed: bool | None
    entry_matches_current: bool | None
    config_supported: bool
    handshake: HandshakeState
    handshake_reason: str | None
    handshake_checked_at: float | None
    engine_online: bool | None
    other_server_count: int | None
    backup_count: int
    notes: tuple[str, ...]


@dataclass(frozen=True)
class OperationResult:
    client_id: str
    operation: str
    performed: bool
    installed: bool | None
    entry_matches_current: bool | None
    handshake: HandshakeState
    handshake_reason: str | None
    handshake_checked_at: float | None
    backup_name: str | None = None
    restored_from: str | None = None
    safety_backup: str | None = None
    removed_target: bool | None = None
    copyable: CopyableConfig | None = None
    message: str = ""


def kernel_verifier() -> HandshakeVerifier:
    """Production verifier: run the handshake kernel like a real client."""

    async def _verify(payload: EntryPayload) -> HandshakeResult:
        env = os.environ.copy()
        env.update(dict(payload.env))
        return await verify_bridge_handshake(payload.command, list(payload.args), env)

    return _verify


def default_payload(engine_port: int = _DEFAULT_PORT) -> EntryPayload:
    """The frozen entry payload (contract §3): no tokens, env only when needed."""
    env: tuple[tuple[str, str], ...] = ()
    if engine_port != _DEFAULT_PORT:
        env = (("EVOBLUE_ENGINE_PORT", str(engine_port)),)
    return EntryPayload(command=sys.executable, args=("-m", "evoblue_video_mcp.mcp"), env=env)


class ClientConfigService:
    def __init__(
        self,
        *,
        payload: EntryPayload,
        verifier: HandshakeVerifier,
        engine_probe: Callable[[], Awaitable[bool | None]] | None = None,
        home: Path | None = None,
        appdata: str | None = None,
        cli_runner: CliRunner | None = None,
    ) -> None:
        self._payload = payload
        self._verifier = verifier
        self._engine_probe = engine_probe
        self._home = home or Path.home()
        self._appdata = appdata if appdata is not None else os.environ.get("APPDATA")
        self._cli_runner = cli_runner or CliRunner()
        self._adapters: dict[str, ClientAdapter] = {
            spec.client_id: self._build_adapter(spec) for spec in CLIENT_SPECS
        }
        self._locks: dict[str, asyncio.Lock] = {}
        self._verdicts: dict[str, tuple[HandshakeState, str | None, float]] = {}

    # -- construction ----------------------------------------------------

    def _build_adapter(self, spec: ClientSpec) -> ClientAdapter:
        if spec.tier == "file_auto":
            json_indent = 2 if spec.client_id != "claude_desktop" else None
            return FileAutoAdapter(
                spec,
                verifier=self._verifier,
                json_indent=json_indent,
                home=self._home,
                appdata=self._appdata,
            )
        if spec.tier == "cli":
            return ClaudeCodeAdapter(
                spec, verifier=self._verifier, runner=self._cli_runner
            )
        return DeepSeekAdapter(spec, verifier=self._verifier)

    # -- helpers ---------------------------------------------------------

    def _spec(self, client_id: str) -> ClientSpec:
        for spec in CLIENT_SPECS:
            if spec.client_id == client_id:
                return spec
        raise ClientUnknownError(f"unknown client: {client_id}")

    def _lock(self, client_id: str) -> asyncio.Lock:
        if client_id not in self._locks:
            self._locks[client_id] = asyncio.Lock()
        return self._locks[client_id]

    def _acquire_lock(self, client_id: str) -> asyncio.Lock:
        lock = self._lock(client_id)
        if lock.locked():
            raise OperationInProgressError(
                f"another operation on {client_id} is still running"
            )
        return lock

    async def _run_handshake(self, client_id: str) -> tuple[HandshakeState, str | None]:
        """Real handshake, verdict cached. failed = executed and failed."""
        verdict: tuple[HandshakeState, str | None]
        try:
            result = await asyncio.wait_for(
                self._verifier(self._payload), _HANDSHAKE_TIMEOUT_S
            )
        except TimeoutError:
            verdict = ("failed", "timeout")
        except Exception:
            verdict = ("unverified", "verify_error")
        else:
            verdict = ("verified", None) if result.ok else ("failed", result.error)
        self._verdicts[client_id] = (verdict[0], verdict[1], time.time())
        return verdict

    def _cached_verdict(
        self, client_id: str
    ) -> tuple[HandshakeState, str | None, float | None]:
        cached = self._verdicts.get(client_id)
        if cached is None:
            return ("unverified", None, None)
        return cached

    def _forget_verdict(self, client_id: str) -> None:
        self._verdicts.pop(client_id, None)

    def _copyable(self, client_id: str) -> CopyableConfig:
        spec = self._spec(client_id)
        adapter = self._adapters[client_id]
        return CopyableConfig(
            client_id=client_id,
            format=spec.container,
            config_text=adapter.copyable_text(self._payload),
            target_path=spec.write_target,
            steps=adapter.copyable_steps(),
        )

    @staticmethod
    def _unverified(outcome: OperationOutcome) -> tuple[HandshakeState, str | None]:
        reason = outcome.reason
        return ("unverified", reason)

    async def _engine_online(self) -> bool | None:
        if self._engine_probe is None:
            return None
        try:
            return await self._engine_probe()
        except Exception:
            return False

    def _operation_result(
        self,
        client_id: str,
        operation: str,
        outcome: OperationOutcome,
        verdict: tuple[str, str | None],
        *,
        include_copyable: bool,
        message: str,
        backup_name: str | None = None,
        restored_from: str | None = None,
        safety_backup: str | None = None,
        removed_target: bool | None = None,
    ) -> OperationResult:
        state, reason, checked_at = self._cached_verdict(client_id)
        if verdict[0] == "unverified" and verdict[1] is not None and state == "unverified":
            reason = verdict[1]
        return OperationResult(
            client_id=client_id,
            operation=operation,
            performed=outcome.performed,
            installed=outcome.installed,
            entry_matches_current=outcome.entry_matches_current,
            handshake=state,
            handshake_reason=reason,
            handshake_checked_at=checked_at,
            backup_name=backup_name,
            restored_from=restored_from,
            safety_backup=safety_backup,
            removed_target=removed_target,
            copyable=self._copyable(client_id) if include_copyable else None,
            message=message,
        )

    # -- queries ---------------------------------------------------------

    def _reconciled_verdict(
        self, client_id: str, state: TargetState
    ) -> tuple[HandshakeState, str | None, float | None]:
        """Cached verdict, invalidated when the config no longer backs it.

        A stored 「已验证」 refers to a specific configuration state; if the
        entry has since changed, disappeared, or become unparseable, keeping
        the verdict would report success next to evidence of change.
        """
        cached = self._verdicts.get(client_id)
        if (
            cached is not None
            and cached[0] == "verified"
            and state.entry_matches_current is not True
        ):
            self._forget_verdict(client_id)
            return ("unverified", "config_changed", None)
        return self._cached_verdict(client_id)

    async def status(self, client_id: str) -> ClientStatus:
        spec = self._spec(client_id)
        adapter = self._adapters[client_id]
        state = await adapter.state(self._payload)
        handshake, reason, checked_at = self._reconciled_verdict(client_id, state)
        return ClientStatus(
            client_id=spec.client_id,
            display_name=spec.display_name,
            tier=spec.tier,
            target_present=state.present,
            installed=state.installed,
            entry_matches_current=state.entry_matches_current,
            config_supported=state.supported,
            handshake=handshake,
            handshake_reason=reason,
            handshake_checked_at=checked_at,
            engine_online=await self._engine_online(),
            other_server_count=state.other_server_count,
            backup_count=state.backup_count,
            notes=(spec.note,) if spec.note else (),
        )

    async def list_status(self) -> list[ClientStatus]:
        online = await self._engine_online()
        statuses: list[ClientStatus] = []
        for spec in CLIENT_SPECS:
            adapter = self._adapters[spec.client_id]
            state = await adapter.state(self._payload)
            handshake, reason, checked_at = self._reconciled_verdict(
                spec.client_id, state
            )
            statuses.append(
                ClientStatus(
                    client_id=spec.client_id,
                    display_name=spec.display_name,
                    tier=spec.tier,
                    target_present=state.present,
                    installed=state.installed,
                    entry_matches_current=state.entry_matches_current,
                    config_supported=state.supported,
                    handshake=handshake,
                    handshake_reason=reason,
                    handshake_checked_at=checked_at,
                    engine_online=online,
                    other_server_count=state.other_server_count,
                    backup_count=state.backup_count,
                    notes=(spec.note,) if spec.note else (),
                )
            )
        return statuses

    async def backups(self, client_id: str) -> list[BackupInfo]:
        self._spec(client_id)
        return await self._adapters[client_id].backups()

    def copyable(self, client_id: str) -> CopyableConfig:
        self._spec(client_id)
        return self._copyable(client_id)

    # -- mutations -------------------------------------------------------

    async def install(self, client_id: str, *, force: bool = False) -> OperationResult:
        spec = self._spec(client_id)
        if spec.tier == "manual":
            raise AutoInstallUnsupportedError(f"{client_id} only supports copyable config")
        async with self._acquire_lock(client_id):
            outcome = await self._adapters[client_id].install(self._payload, force=force)
            # A handshake verdict requires configuration evidence (contract
            # §6: verified = 写入证明成立 且 真实握手成功 — never either alone).
            if outcome.entry_matches_current is True:
                verdict = await self._run_handshake(client_id)
            else:
                self._forget_verdict(client_id)
                verdict = self._unverified(outcome)
        message = _install_message(outcome, verdict)
        return self._operation_result(
            client_id,
            "install",
            outcome,
            verdict,
            include_copyable=not outcome.performed,
            message=message,
            backup_name=outcome.backup_name,
        )

    async def verify(self, client_id: str) -> OperationResult:
        self._spec(client_id)
        outcome = await self._adapters[client_id].verify(self._payload)
        config_ok = bool(outcome.installed and outcome.entry_matches_current)
        if not config_ok:
            # A failed configuration check invalidates any previous verdict —
            # returning a cached 「已验证」 next to entry_matches_current=false
            # would be exactly the contradiction the contract forbids (§6).
            self._forget_verdict(client_id)
            # manual tier has no client-side configuration evidence at all,
            # so a payload handshake can never make it verified (它只证明
            # Bridge 能启动, 不证明客户端已配置).
            verdict = self._unverified(outcome)
        else:
            verdict = await self._run_handshake(client_id)
        return self._operation_result(
            client_id,
            "verify",
            outcome,
            verdict,
            include_copyable=not config_ok,
            message=_verify_message(outcome, verdict),
        )

    async def remove(self, client_id: str, *, confirm: bool) -> OperationResult:
        spec = self._spec(client_id)
        if not confirm:
            raise ConfirmationRequiredError("remove requires confirm:true")
        if spec.tier == "manual":
            raise AutoInstallUnsupportedError(f"{client_id} only supports copyable config")
        async with self._acquire_lock(client_id):
            outcome = await self._adapters[client_id].remove(self._payload)
            self._forget_verdict(client_id)
            verdict = ("unverified", outcome.reason or "not_installed")
        return self._operation_result(
            client_id,
            "remove",
            outcome,
            verdict,
            include_copyable=True,
            message=(
                "已移除配置（可从备份恢复）。"
                if outcome.performed
                else "未执行移除：配置本就不存在。"
            ),
            removed_target=outcome.removed_target,
            backup_name=outcome.backup_name,
        )

    async def restore(self, client_id: str, backup_name: str, *, confirm: bool) -> OperationResult:
        spec = self._spec(client_id)
        if not confirm:
            raise ConfirmationRequiredError("restore requires confirm:true")
        if spec.tier == "manual":
            raise AutoInstallUnsupportedError(f"{client_id} only supports copyable config")
        async with self._acquire_lock(client_id):
            outcome = await self._adapters[client_id].restore(self._payload, backup_name)
            self._forget_verdict(client_id)
            verdict = ("unverified", "restored_run_verify")
        message = (
            f"已恢复 {outcome.restored_from}（本次操作的安全备份：{outcome.safety_backup}）。"
            "请点「测试连接」完成真实握手验证。"
            if outcome.performed
            else "未执行恢复。"
        )
        return self._operation_result(
            client_id,
            "restore",
            outcome,
            verdict,
            include_copyable=False,
            message=message,
            restored_from=outcome.restored_from,
            safety_backup=outcome.safety_backup,
        )


def _install_message(outcome: OperationOutcome, verdict: tuple[str, str | None]) -> str:
    if verdict[0] == "verified":
        return "已安装并通过真实握手验证。"
    if verdict[0] == "failed":
        return f"配置已写入，但真实握手失败（{verdict[1]}）。请检查 Engine 是否在运行。"
    if outcome.performed:
        return f"命令已执行，但未能确认配置（{outcome.reason or 'cli_output_unparsable'}）。"
    if outcome.reason == "already_configured":
        return "配置已是目标状态，未重复写入。"
    if outcome.reason == "client_directory_missing":
        return "未找到客户端目录（客户端可能未安装），未写入。可复制下面的配置手动完成。"
    return f"未完成：{outcome.reason or '未验证'}。"


def _verify_message(outcome: OperationOutcome, verdict: tuple[str, str | None]) -> str:
    if verdict[0] == "verified":
        return "真实握手通过。"
    if verdict[0] == "failed":
        return f"真实握手失败（{verdict[1]}）。"
    if outcome.reason == "not_installed":
        return "未检测到本项目的配置条目。"
    if outcome.reason == "entry_mismatch":
        return "配置条目存在但与目标不一致（可能路径已变化）。"
    return f"未验证：{outcome.reason or '未知原因'}。"
