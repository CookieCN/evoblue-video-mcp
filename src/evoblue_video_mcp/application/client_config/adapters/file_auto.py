"""File-based auto-config adapter — one implementation for codex,
claude_desktop and workbuddy (they differ only in the frozen spec data).

The operation gates (contract §6) live here: resolve → read → merge decision
→ conflict gate → backup → commit → read-back → post proof; the handshake
runs afterwards via the injected verifier (service-side). A post-commit proof
failure auto-restores the pre-operation backup — 失败可恢复 is mechanics,
not copy.

All filesystem primitives route through ``writes``/``backup`` (the structural
gate audits this). Blocking IO is wrapped in ``asyncio.to_thread``.
"""

import asyncio
import codecs
from pathlib import Path

from evoblue_video_mcp.application.client_config.adapters.base import (
    HandshakeVerifier,
    OperationOutcome,
    TargetState,
)
from evoblue_video_mcp.application.client_config.backup import (
    create_backup,
    list_backups,
    restore,
)
from evoblue_video_mcp.application.client_config.errors import (
    ConfigWriteError,
    UnsupportedConfigError,
)
from evoblue_video_mcp.application.client_config.merge_json import (
    merge_json as merge_json_doc,
)
from evoblue_video_mcp.application.client_config.merge_json import (
    payload_entry,
    remove_entry_json,
)
from evoblue_video_mcp.application.client_config.merge_json import (
    prove_merge as prove_merge_json,
)
from evoblue_video_mcp.application.client_config.merge_json import (
    prove_removal as prove_removal_json,
)
from evoblue_video_mcp.application.client_config.merge_json import (
    read_entry as read_entry_json,
)
from evoblue_video_mcp.application.client_config.merge_toml import (
    entry_is_addressable,
    payload_table,
    remove_entry_toml,
)
from evoblue_video_mcp.application.client_config.merge_toml import (
    merge_toml as merge_toml_doc,
)
from evoblue_video_mcp.application.client_config.merge_toml import (
    prove_merge as prove_merge_toml,
)
from evoblue_video_mcp.application.client_config.merge_toml import (
    prove_removal as prove_removal_toml,
)
from evoblue_video_mcp.application.client_config.merge_toml import (
    read_entry as read_entry_toml,
)
from evoblue_video_mcp.application.client_config.models import (
    BackupInfo,
    ClientSpec,
    EntryPayload,
)
from evoblue_video_mcp.application.client_config.paths import resolve_target
from evoblue_video_mcp.application.client_config.render import (
    render_json_fragment,
    render_toml_entry,
)
from evoblue_video_mcp.application.client_config.writes import commit_atomic

_BOM = codecs.BOM_UTF8


class FileAutoAdapter:
    def __init__(
        self,
        spec: ClientSpec,
        *,
        verifier: HandshakeVerifier,
        json_indent: int | None = None,
        home: Path,
        appdata: str | None,
    ) -> None:
        self._spec = spec
        self._verifier = verifier
        self._json_indent = json_indent
        self._home = home
        self._appdata = appdata

    # -- helpers ---------------------------------------------------------

    @property
    def _is_toml(self) -> bool:
        return self._spec.container == "toml"

    def _resolve(self) -> Path | None:
        assert self._spec.write_target is not None
        return resolve_target(
            self._spec.write_target, home=self._home, appdata=self._appdata
        )

    @staticmethod
    def _split_bom(raw: bytes) -> tuple[bytes, str]:
        """Return (bom prefix, decoded text)."""
        if raw.startswith(_BOM):
            return raw[:3], raw.decode("utf-8-sig")
        return b"", raw.decode("utf-8")

    @staticmethod
    def _encode(text: str, bom_prefix: bytes) -> bytes:
        return bom_prefix + text.encode("utf-8")

    @staticmethod
    def _read_bytes(target: Path) -> bytes | None:
        try:
            return target.read_bytes()
        except FileNotFoundError:
            return None

    def _merge(
        self, text: str, payload: EntryPayload, *, force: bool
    ) -> tuple[str, bool]:
        if self._is_toml:
            toml_outcome = merge_toml_doc(text, self._spec.entry_key, payload, force=force)
            return toml_outcome.text, toml_outcome.changed
        json_outcome = merge_json_doc(
            text,
            self._spec.entry_key,
            payload,
            indent=self._json_indent or 2,
            force=force,
        )
        return json_outcome.text, json_outcome.changed

    def _remove(self, text: str) -> tuple[str, bool]:
        if self._is_toml:
            toml_outcome = remove_entry_toml(text, self._spec.entry_key)
            return toml_outcome.text, toml_outcome.changed
        json_outcome = remove_entry_json(
            text, self._spec.entry_key, indent=self._json_indent or 2
        )
        return json_outcome.text, json_outcome.changed

    def _read_entry(self, text: str) -> dict[str, object] | None:
        if self._is_toml:
            entry = read_entry_toml(text, self._spec.entry_key)
            if entry is not None and not entry_is_addressable(text, self._spec.entry_key):
                # Dotted-key definitions support no operation (remove has no
                # span) — fail closed instead of verifying then dead-ending.
                raise UnsupportedConfigError(
                    "entry exists but has no [mcp_servers.<key>] header"
                )
            return entry
        return read_entry_json(text, self._spec.entry_key)

    def _expected(self, payload: EntryPayload) -> dict[str, object]:
        if self._is_toml:
            return payload_table(payload)
        return payload_entry(payload)

    def _post_merge_proof(
        self, original_text: str, written_text: str, payload: EntryPayload
    ) -> None:
        """Re-run the parse-level proof against the bytes actually on disk."""
        if self._is_toml:
            prove_merge_toml(
                original_text, written_text, self._spec.entry_key, payload_table(payload)
            )
        else:
            prove_merge_json(
                original_text, written_text, self._spec.entry_key, payload_entry(payload)
            )

    def _parse_proof(self, text: str) -> None:
        """Parse proof on restored bytes (contract §5): the document must
        parse and our entry (if present) must be readable."""
        self._read_entry(text)

    # -- operations ------------------------------------------------------

    async def install(self, payload: EntryPayload, *, force: bool) -> OperationOutcome:
        return await asyncio.to_thread(self._install_sync, payload, force)

    def _install_sync(self, payload: EntryPayload, force: bool) -> OperationOutcome:
        target = self._resolve()
        if target is None:
            return OperationOutcome(performed=False, reason="client_unsupported")
        if not target.parent.is_dir():
            return OperationOutcome(performed=False, reason="client_directory_missing")
        raw = self._read_bytes(target)
        if raw is None:
            # Absent target = fresh client config, not an unsupported file
            # (contract §4.2's "empty file" gate is for EXISTING files).
            bom_prefix, original_text = b"", ("{}" if not self._is_toml else "")
        else:
            bom_prefix, original_text = self._split_bom(raw)
        # Merge raises UnsupportedConfigError / EntryConflictError (fail closed)
        # before anything touches the disk.
        merged_text, changed = self._merge(original_text, payload, force=force)
        if not changed:
            return OperationOutcome(
                performed=False,
                reason="already_configured",
                installed=True,
                entry_matches_current=True,
            )
        backup = create_backup(target)
        try:
            commit_atomic(target, self._encode(merged_text, bom_prefix))
            written = self._read_bytes(target)
            written_text = "" if written is None else self._decode_text(written)
            self._post_merge_proof(original_text, written_text, payload)
        except BaseException:
            # 失败可恢复: put the pre-operation state back before surfacing.
            restore(target, backup.name)
            raise ConfigWriteError(
                f"post-write verification failed; rolled back to {backup.name}"
            ) from None
        return OperationOutcome(
            performed=True,
            installed=True,
            entry_matches_current=True,
            backup_name=backup.name,
        )

    @staticmethod
    def _decode_text(raw: bytes) -> str:
        return raw.decode("utf-8-sig")

    async def verify(self, payload: EntryPayload) -> OperationOutcome:
        target = self._resolve()
        if target is None:
            return OperationOutcome(performed=False, reason="client_unsupported")
        raw = self._read_bytes(target)
        if raw is None:
            return OperationOutcome(
                performed=False, reason="not_installed", installed=False
            )
        entry = self._read_entry(self._decode_text(raw))
        if entry is None:
            return OperationOutcome(
                performed=False,
                reason="not_installed",
                installed=False,
                entry_matches_current=False,
            )
        matches = entry == self._expected(payload)
        return OperationOutcome(
            performed=False,
            reason=None if matches else "entry_mismatch",
            installed=True,
            entry_matches_current=matches,
        )

    async def remove(self, payload: EntryPayload) -> OperationOutcome:
        return await asyncio.to_thread(self._remove_sync)

    def _remove_sync(self) -> OperationOutcome:
        target = self._resolve()
        if target is None:
            return OperationOutcome(performed=False, reason="client_unsupported")
        raw = self._read_bytes(target)
        if raw is None:
            return OperationOutcome(
                performed=False, reason="not_installed", installed=False
            )
        bom_prefix, original_text = self._split_bom(raw)
        if self._read_entry(original_text) is None:
            return OperationOutcome(
                performed=False, reason="not_installed", installed=False
            )
        merged_text, changed = self._remove(original_text)
        if not changed:
            return OperationOutcome(
                performed=False, reason="not_installed", installed=False
            )
        backup = create_backup(target)
        try:
            commit_atomic(target, self._encode(merged_text, bom_prefix))
            written = self._read_bytes(target)
            written_text = "" if written is None else self._decode_text(written)
            if self._is_toml:
                prove_removal_toml(original_text, written_text, self._spec.entry_key)
            else:
                prove_removal_json(original_text, written_text, self._spec.entry_key)
        except BaseException:
            restore(target, backup.name)
            raise ConfigWriteError(
                f"post-removal verification failed; rolled back to {backup.name}"
            ) from None
        return OperationOutcome(
            performed=True,
            installed=False,
            backup_name=backup.name,
            removed_target=False,
        )

    async def restore(self, payload: EntryPayload, backup_name: str) -> OperationOutcome:
        return await asyncio.to_thread(self._restore_sync, backup_name)

    def _restore_sync(self, backup_name: str) -> OperationOutcome:
        target = self._resolve()
        if target is None:
            return OperationOutcome(performed=False, reason="client_unsupported")
        result = restore(target, backup_name)
        if not result.removed_target:
            written = self._read_bytes(target)
            if written is not None:
                self._parse_proof(self._decode_text(written))
        return OperationOutcome(
            performed=True,
            restored_from=result.restored_from,
            safety_backup=result.safety_backup,
            removed_target=result.removed_target,
        )

    # -- inspection ------------------------------------------------------

    async def state(self, payload: EntryPayload) -> TargetState:
        return await asyncio.to_thread(self._state_sync, payload)

    def _state_sync(self, payload: EntryPayload) -> TargetState:
        target = self._resolve()
        if target is None:
            return TargetState(
                supported=False,
                present=None,
                installed=None,
                entry_matches_current=None,
                other_server_count=None,
                backup_count=0,
            )
        backups = len(list_backups(target))
        raw = self._read_bytes(target)
        if raw is None:
            return TargetState(
                supported=True,
                present=False,
                installed=False,
                entry_matches_current=False,
                other_server_count=0,
                backup_count=backups,
            )
        try:
            text = self._decode_text(raw)
            entry = self._read_entry(text)
            return TargetState(
                supported=True,
                present=True,
                installed=entry is not None,
                entry_matches_current=entry == self._expected(payload),
                other_server_count=_registry_size(text, self._spec.container, self._spec.entry_key),
                backup_count=backups,
            )
        except Exception:
            return TargetState(
                supported=True,
                present=True,
                installed=None,
                entry_matches_current=None,
                other_server_count=None,
                backup_count=backups,
            )

    async def backups(self) -> list[BackupInfo]:
        target = self._resolve()
        if target is None:
            return []
        return await asyncio.to_thread(lambda: list_backups(target))

    def copyable_text(self, payload: EntryPayload) -> str:
        if self._is_toml:
            return render_toml_entry(self._spec.entry_key, payload)
        return render_json_fragment(self._spec.entry_key, payload, indent=self._json_indent)

    def copyable_steps(self) -> tuple[str, ...]:
        if self._is_toml:
            return ("把下面的表追加到 config.toml 末尾，保存后重启 Codex。",)
        if self._spec.client_id == "claude_desktop":
            return (
                "把 mcpServers 中缺失的部分合并进 claude_desktop_config.json"
                "（保留其他键），保存后重启 Claude Desktop。",
            )
        return (
            "把 mcpServers 中缺失的部分合并进 mcp.json（保留其他键），保存后在 "
            "WorkBuddy 连接器管理页对自定义连接器手动 Trust。",
        )


def _registry_size(text: str, container: str, our_key: str) -> int | None:
    """Count of registry entries other than ours (field-level only, §8)."""
    try:
        if container == "toml":
            import tomllib

            doc = tomllib.loads(text)
            servers = doc.get("mcp_servers")
        else:
            import json

            doc = json.loads(text)
            servers = doc.get("mcpServers")
    except Exception:
        return None
    if not isinstance(servers, dict):
        return None
    return max(0, len(servers) - (1 if our_key in servers else 0))
