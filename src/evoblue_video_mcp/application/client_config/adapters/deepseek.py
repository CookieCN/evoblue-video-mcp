"""Manual tier adapter — DeepSeek renders copyable config and nothing else.

Format still changing and no local install (CLIENT_COMPATIBILITY): no auto
write is ever attempted (contract §0/§1), every mutation is rejected at the
service boundary with ``AUTO_INSTALL_UNSUPPORTED`` (409). ``verify`` is
read-only and genuinely useful: it handshakes the payload so a user who
configured the client by hand can check the result.
"""

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
from evoblue_video_mcp.application.client_config.render import render_json_fragment

_MANUAL_STEPS = (
    "DeepSeek 的配置格式仍在变化，请在其官方 MCP Client 插件设置中手动添加 STDIO 条目。",
    "完成后回到本页点「测试连接」，以真实握手结果为准。",
)


class DeepSeekAdapter:
    def __init__(
        self, spec: ClientSpec, *, verifier: HandshakeVerifier, json_indent: int = 2
    ) -> None:
        self._spec = spec
        self._verifier = verifier
        self._json_indent = json_indent

    async def install(self, payload: EntryPayload, *, force: bool) -> OperationOutcome:
        del payload, force
        return OperationOutcome(performed=False, reason="manual_tier")

    async def verify(self, payload: EntryPayload) -> OperationOutcome:
        # Read-only: nothing to inspect, so configuration evidence is unknown;
        # the handshake itself is still the honest check of the payload.
        return OperationOutcome(performed=False, installed=None, reason="manual_tier")

    async def remove(self, payload: EntryPayload) -> OperationOutcome:
        del payload
        return OperationOutcome(performed=False, reason="manual_tier")

    async def restore(self, payload: EntryPayload, backup_name: str) -> OperationOutcome:
        del payload, backup_name
        return OperationOutcome(performed=False, reason="manual_tier")

    async def state(self, payload: EntryPayload) -> TargetState:
        del payload
        return TargetState(
            supported=True,
            present=None,
            installed=None,
            entry_matches_current=None,
            other_server_count=None,
            backup_count=0,
        )

    async def backups(self) -> list[BackupInfo]:
        return []

    def copyable_text(self, payload: EntryPayload) -> str:
        return render_json_fragment(self._spec.entry_key, payload, indent=self._json_indent)

    def copyable_steps(self) -> tuple[str, ...]:
        return _MANUAL_STEPS
