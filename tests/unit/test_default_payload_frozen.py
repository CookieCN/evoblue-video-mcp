"""P7-004: the frozen payload branch (INSTALLER_RELEASE_CONTRACT §5).

Source-form payloads must stay byte-identical to the P4-frozen shape; the
frozen branch only activates inside PyInstaller builds (sys.frozen), which the
tests simulate with monkeypatch.
"""

import sys

from evoblue_video_mcp.application.client_config.service import (
    FROZEN_BRIDGE_ARGS,
    default_payload,
)


def test_source_payload_unchanged() -> None:
    payload = default_payload()
    assert payload.command == sys.executable
    assert tuple(payload.args) == ("-m", "evoblue_video_mcp.mcp")
    assert payload.env == ()


def test_source_payload_nondefault_port_env() -> None:
    payload = default_payload(9000)
    assert payload.env == (("EVOBLUE_ENGINE_PORT", "9000"),)


def test_frozen_payload_uses_bridge_subcommand(monkeypatch, tmp_path) -> None:
    fake_exe = tmp_path / "evoblue-engine-full.exe"
    fake_exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    payload = default_payload()
    assert payload.command == str(fake_exe)
    assert tuple(payload.args) == FROZEN_BRIDGE_ARGS == ("bridge",)
    assert payload.env == ()


def test_frozen_payload_keeps_port_env(monkeypatch, tmp_path) -> None:
    fake_exe = tmp_path / "evoblue-engine-full.exe"
    fake_exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    payload = default_payload(9000)
    assert payload.env == (("EVOBLUE_ENGINE_PORT", "9000"),)
