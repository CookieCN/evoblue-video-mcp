"""P5-004 integration: /api/mcp-clients endpoints against a scripted service.

Real filesystem (tmp_path home), real merge/write/backup code, scripted
handshake verifier and a missing `claude` CLI — the wire contract plus the
「UI 不虚报成功」 semantics end to end (contract §7/§8). The real stdio
handshake is covered in test_client_config_handshake.py.
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

from evoblue_video_mcp.application.client_config.adapters.claude_code import (
    CliResult,
    CliRunner,
    CliUnavailableError,
)
from evoblue_video_mcp.application.client_config.models import EntryPayload
from evoblue_video_mcp.application.client_config.service import (
    ClientConfigService,
    default_payload,
)
from evoblue_video_mcp.application.handshake import HandshakeResult
from evoblue_video_mcp.web.app import create_app

_TOKEN = "p5-api-token"


def _ok_result() -> HandshakeResult:
    return HandshakeResult(
        ok=True,
        error="ok",
        server_name="evoblue-video",
        protocol_version="2025-11-25",
        tool_names=("a", "b"),
        stderr_tail=None,
        elapsed_s=0.01,
    )


def _fail_result(error: str) -> HandshakeResult:
    return HandshakeResult(
        ok=False,
        error=error,  # type: ignore[arg-type]
        server_name=None,
        protocol_version=None,
        tool_names=(),
        stderr_tail=None,
        elapsed_s=0.01,
    )


class _MissingCliRunner(CliRunner):
    """A machine where `claude` is not on PATH."""

    def __init__(self) -> None:
        super().__init__(program="definitely-not-on-path-xyz")

    async def run(self, args: Any) -> CliResult:
        raise CliUnavailableError("no cli in tests")


def _service(
    home: Path,
    verifier: Callable[[EntryPayload], Awaitable[HandshakeResult]],
    runner: CliRunner | None = None,
) -> ClientConfigService:
    return ClientConfigService(
        payload=default_payload(),
        verifier=verifier,
        home=home,
        appdata=str(home / "AppData"),
        cli_runner=runner or _MissingCliRunner(),
    )


def _app(
    home: Path,
    verifier: Callable[[EntryPayload], Awaitable[HandshakeResult]],
    runner: CliRunner | None = None,
):
    return create_app(
        local_token=_TOKEN,
        client_config_service=_service(home, verifier, runner),
    )


async def _ok(payload: EntryPayload) -> HandshakeResult:
    del payload
    return _ok_result()




async def test_bare_app_has_no_client_config_endpoints() -> None:
    app = create_app()  # no service → no routes
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/mcp-clients")
    assert response.status_code == 404


async def test_endpoints_require_the_local_token(tmp_path: Path) -> None:
    app = _app(tmp_path, _ok)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/mcp-clients")
    assert response.status_code == 401


async def test_list_reports_field_level_status_only(tmp_path: Path) -> None:
    """No whole-file echo, no foreign server names — counts only (§8)."""
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text(
        '[mcp_servers.other-server]\ncommand = "node"\nargs = []\n'
        "SECRET_API_KEY = 'do-not-leak'\n",
        encoding="utf-8",
    )
    app = _app(home, _ok)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/mcp-clients", headers={"X-Local-Token": _TOKEN}
        )
    assert response.status_code == 200
    raw = response.text
    assert "other-server" not in raw
    assert "do-not-leak" not in raw
    assert str(tmp_path) not in raw
    body = response.json()
    assert [c["client_id"] for c in body["clients"]] == [
        "codex",
        "claude_desktop",
        "workbuddy",
        "claude_code",
        "deepseek",
    ]
    codex = next(c for c in body["clients"] if c["client_id"] == "codex")
    assert codex["other_server_count"] == 1
    assert codex["installed"] is False
    assert codex["handshake"] == "unverified"  # nothing ran yet — honest default


async def test_install_happy_path_writes_and_verifies(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    app = _app(home, _ok)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/mcp-clients/codex/install",
            headers={"X-Local-Token": _TOKEN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["performed"] is True
    assert body["handshake"] == "verified"
    assert body["backup_name"] is not None
    assert "verified" in body["message"] or "已安装" in body["message"]
    written = (home / ".codex" / "config.toml").read_bytes()
    assert b"[mcp_servers.evoblue-video]" in written


async def test_install_failed_handshake_reports_failed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".workbuddy").mkdir(parents=True)

    async def fail(payload: EntryPayload) -> HandshakeResult:
        return _fail_result("name_mismatch")

    app = _app(home, fail)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/mcp-clients/workbuddy/install",
            headers={"X-Local-Token": _TOKEN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["performed"] is True
    assert body["handshake"] == "failed"
    assert body["handshake_reason"] == "name_mismatch"


async def test_install_with_broken_verifier_is_unverified(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)

    async def broken(payload: EntryPayload) -> HandshakeResult:
        raise RuntimeError("verifier exploded")

    app = _app(home, broken)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/mcp-clients/codex/install",
            headers={"X-Local-Token": _TOKEN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["handshake"] == "unverified"
    assert body["handshake_reason"] == "verify_error"
    assert body["installed"] is True


async def test_install_conflict_then_force(tmp_path: Path) -> None:
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text(
        '[mcp_servers.evoblue-video]\ncommand = "old"\nargs = []\n'
        'startup_timeout_ms = 9000\n',
        encoding="utf-8",
    )
    app = _app(home, _ok)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        conflict = await client.post(
            "/api/mcp-clients/codex/install",
            headers={"X-Local-Token": _TOKEN},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "ENTRY_MODIFIED"

        forced = await client.post(
            "/api/mcp-clients/codex/install",
            headers={"X-Local-Token": _TOKEN},
            json={"force": True},
        )
    assert forced.status_code == 200
    assert forced.json()["performed"] is True
    content = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "startup_timeout_ms" not in content  # force accepted the loss


async def test_remove_requires_confirm(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", _ok)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/mcp-clients/codex/remove",
            headers={"X-Local-Token": _TOKEN},
            json={"confirm": False},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONFIRMATION_REQUIRED"


async def test_remove_round_trip_with_restore(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '# keep\n[mcp_servers.evoblue-video]\ncommand = "old"\nargs = []\n',
        encoding="utf-8",
    )
    app = _app(home, _ok)
    headers = {"X-Local-Token": _TOKEN}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        removed = await client.post(
            "/api/mcp-clients/codex/remove",
            headers=headers,
            json={"confirm": True},
        )
        assert removed.status_code == 200
        assert removed.json()["performed"] is True
        content = config.read_text(encoding="utf-8")
        assert "evoblue-video" not in content
        assert content.startswith("# keep\n")

        backups = (await client.get(
            "/api/mcp-clients/codex/backups", headers=headers
        )).json()
        assert backups["items"], "remove must have taken a backup"

        unknown = await client.post(
            "/api/mcp-clients/codex/restore",
            headers=headers,
            json={
                "backup_name": "config.toml.evoblue-backup-20990101T000000Z-beef",
                "confirm": True,
            },
        )
        assert unknown.status_code == 404

        restored = await client.post(
            "/api/mcp-clients/codex/restore",
            headers=headers,
            json={"backup_name": backups["items"][0]["name"], "confirm": True},
        )
        assert restored.status_code == 200
        assert restored.json()["restored_from"] == backups["items"][0]["name"]
        assert restored.json()["safety_backup"]
        assert '[mcp_servers.evoblue-video]' in config.read_text(encoding="utf-8")


async def test_manual_tier_mutations_are_rejected(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", _ok)
    headers = {"X-Local-Token": _TOKEN}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        responses = [
            await client.post(
                "/api/mcp-clients/deepseek/install", headers=headers
            ),
            await client.post(
                "/api/mcp-clients/deepseek/remove", headers=headers, json={"confirm": True}
            ),
            await client.post(
                "/api/mcp-clients/deepseek/restore",
                headers=headers,
                json={"backup_name": "x", "confirm": True},
            ),
        ]
        for response in responses:
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "AUTO_INSTALL_UNSUPPORTED"


async def test_cli_tier_downgrades_to_copyable_without_cli(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", _ok)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/mcp-clients/claude_code/install",
            headers={"X-Local-Token": _TOKEN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["performed"] is False
    assert body["handshake_reason"] == "claude_cli_not_found"
    assert body["copyable"] is not None
    assert body["copyable"]["config_text"].startswith("claude mcp add evoblue-video")


class _FakeCliRunner(CliRunner):
    """Scripted `claude` CLI: add ok, get confirms, remove ok."""

    def __init__(self, command: str) -> None:
        super().__init__(program="fake-claude")
        self._command = command
        self.calls: list[list[str]] = []

    async def run(self, args: Sequence[str]) -> CliResult:
        self.calls.append(list(args))
        if args[0] == "mcp" and args[1] == "add":
            return CliResult(returncode=0, stdout="added", stderr="")
        if args[0] == "mcp" and args[1] == "get":
            # real-shape (2026-09-07, CLI 2.1.261): no scope flag; the output
            # lists Scope / Command / Args separately; drive case may differ.
            return CliResult(
                returncode=0,
                stdout=(
                    "evoblue-video:\n"
                    "  Scope: Local config (private to you in this project)\n"
                    "  Status: Connected\n"
                    "  Type: stdio\n"
                    f"  Command: {self._command.lower()}\n"
                    "  Args: -m evoblue_video_mcp.mcp\n"
                ),
                stderr="",
            )
        if args[0] == "mcp" and args[1] == "remove":
            return CliResult(returncode=0, stdout="removed", stderr="")
        return CliResult(returncode=1, stdout="", stderr="unexpected")


async def test_cli_tier_happy_path_installs_and_verifies(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runner = _FakeCliRunner(default_payload().command)
    service = _service(home, _ok, runner)
    result = await service.install("claude_code")
    assert result.performed is True
    assert result.handshake == "verified"
    add_call = runner.calls[0]
    assert add_call[:2] == ["mcp", "add"]
    assert "--scope" in add_call and "local" in add_call
    assert default_payload().command in add_call and "-m" in add_call

    removed = await service.remove("claude_code", confirm=True)
    assert removed.performed is True
    assert removed.installed is False


async def test_unknown_client_is_404(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", _ok)
    headers = {"X-Local-Token": _TOKEN}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        status = await client.get("/api/mcp-clients/nope", headers=headers)
        assert status.status_code == 404
        assert status.json()["error"]["code"] == "CLIENT_UNKNOWN"


async def test_manual_tier_verify_never_verifies(tmp_path: Path) -> None:
    """P1 regression: no client-side evidence exists for manual tier, so a
    payload handshake must not produce verified (contract §6)."""
    called = {"handshake": False}

    async def verifier(payload: EntryPayload) -> HandshakeResult:
        called["handshake"] = True
        return _ok_result()

    home = tmp_path / "home"
    app = create_app(local_token=_TOKEN, client_config_service=_service(home, verifier))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/mcp-clients/deepseek/verify", headers={"X-Local-Token": _TOKEN}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["handshake"] == "unverified"
    assert body["handshake_reason"] == "manual_tier"
    assert called["handshake"] is False


async def test_failed_config_check_invalidates_cached_verified(tmp_path: Path) -> None:
    """P1 regression: externally modifying the config after a verified
    handshake must not leave verified next to entry_matches_current=false."""
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    config = home / ".codex" / "config.toml"
    app = _app(home, _ok)
    headers = {"X-Local-Token": _TOKEN}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/api/mcp-clients/codex/install", headers=headers
        )
        assert first.json()["handshake"] == "verified"

        config.write_text(
            '[mcp_servers.evoblue-video]\ncommand = "tampered"\nargs = []\n',
            encoding="utf-8",
        )
        after = await client.post("/api/mcp-clients/codex/verify", headers=headers)
        body = after.json()
        assert body["entry_matches_current"] is False
        assert body["handshake"] == "unverified", body

        status = await client.get("/api/mcp-clients/codex", headers=headers)
        assert status.json()["handshake"] == "unverified"


async def test_cli_tier_wrong_args_is_not_verified(tmp_path: Path) -> None:
    """P1 regression: right python + wrong module/scope must not verify."""

    class _WrongArgsRunner(_FakeCliRunner):
        async def run(self, args: Any) -> CliResult:
            result = await super().run(args)
            if args[0] == "mcp" and args[1] == "get":
                return CliResult(
                    returncode=0,
                    stdout=(
                        "evoblue-video:\n"
                        "  Scope: User config\n"
                        "  Status: Connected\n"
                        "  Type: stdio\n"
                        f"  Command: {self._command.lower()}\n"
                        "  Args: -m wrong.module\n"
                    ),
                    stderr="",
                )
            return result

    service = _service(
        tmp_path / "home", _ok, _WrongArgsRunner(default_payload().command)
    )
    result = await service.verify("claude_code")
    assert result.entry_matches_current is False
    assert result.handshake == "unverified"
    assert result.handshake_reason == "entry_mismatch"


async def test_validation_failures_use_the_frozen_p5_codes(tmp_path: Path) -> None:
    """P2 regression: P5 body-validation failures answer INVALID_REQUEST, and
    a missing remove body still flows into CONFIRMATION_REQUIRED."""
    home = tmp_path / "home"
    app = _app(home, _ok)
    headers = {"X-Local-Token": _TOKEN}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        malformed = await client.post(
            "/api/mcp-clients/codex/install",
            headers={**headers, "Content-Type": "application/json"},
            content="{broken",
        )
        assert malformed.status_code == 422
        assert malformed.json()["error"]["code"] == "INVALID_REQUEST"

        no_body = await client.post(
            "/api/mcp-clients/codex/remove", headers=headers
        )
        assert no_body.status_code == 422
        assert no_body.json()["error"]["code"] == "CONFIRMATION_REQUIRED"


async def test_copyable_config_endpoint(tmp_path: Path) -> None:
    home = tmp_path / "home"
    app = _app(home, _ok)
    headers = {"X-Local-Token": _TOKEN}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        codex = await client.get("/api/mcp-clients/codex/config", headers=headers)
        assert codex.status_code == 200
        assert codex.json()["format"] == "toml"
        assert "[mcp_servers.evoblue-video]" in codex.json()["config_text"]

        workbuddy = await client.get("/api/mcp-clients/workbuddy/config", headers=headers)
        assert workbuddy.json()["format"] == "json"
        fragment = json.loads(workbuddy.json()["config_text"])
        assert "evoblue-video-mcp" in fragment["mcpServers"]
