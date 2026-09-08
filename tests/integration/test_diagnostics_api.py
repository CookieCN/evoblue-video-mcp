"""P4-002: GET /api/diagnostics — auth, envelope, redaction, network gating."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from evoblue_video_mcp import __version__
from evoblue_video_mcp.application.diagnostics import CHECK_NAMES
from evoblue_video_mcp.storage.repository import save_app_settings
from evoblue_video_mcp.web.app import create_app

_TOKEN = "diag-token"

_NOW = 1_700_000_000.0


def _client(app: object) -> httpx.AsyncClient:  # type: ignore[type-arg]
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_diagnostics_requires_local_token(
    session_factory: async_sessionmaker,
) -> None:
    app = create_app(session_factory=session_factory, local_token=_TOKEN)
    async with _client(app) as client:
        denied = await client.get("/api/diagnostics")
        assert denied.status_code == 401
        assert denied.json() == {"detail": "invalid local access token"}

        allowed = await client.get(
            "/api/diagnostics", headers={"X-Local-Token": _TOKEN}
        )
        assert allowed.status_code == 200


async def test_diagnostics_payload_matches_frozen_contract(
    session_factory: async_sessionmaker,
) -> None:
    app = create_app(session_factory=session_factory)
    async with _client(app) as client:
        response = await client.get("/api/diagnostics")
    assert response.status_code == 200
    body = response.json()
    assert body["engine_version"] == __version__
    assert body["redacted"] is True
    assert body["overall"] in {"pass", "warning", "fail"}
    assert [check["name"] for check in body["checks"]] == list(CHECK_NAMES)
    by_name = {check["name"]: check for check in body["checks"]}
    assert by_name["llm_api"]["status"] == "skipped"  # include_network defaults off
    for check in body["checks"]:
        assert set(check) == {"name", "status", "message", "detail"}


async def test_diagnostics_parameter_validation_uses_p3_envelope(
    session_factory: async_sessionmaker,
) -> None:
    app = create_app(session_factory=session_factory)
    async with _client(app) as client:
        response = await client.get("/api/diagnostics?include_network=maybe")
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "INVALID_FILTER", "message": "invalid query parameters"}
    }


async def test_diagnostics_never_leaks_absolute_paths(
    session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    async with session_factory() as sess:
        await save_app_settings(
            sess, setup_completed=True, now=_NOW, report_directory=str(tmp_path)
        )
    app = create_app(session_factory=session_factory)
    async with _client(app) as client:
        response = await client.get("/api/diagnostics")
    assert response.status_code == 200
    raw = response.text
    assert str(tmp_path) not in raw
    assert ":\\" not in raw
    report_directory = next(
        check for check in response.json()["checks"] if check["name"] == "report_directory"
    )
    assert report_directory["status"] == "pass"
    assert report_directory["detail"] is not None
    assert report_directory["detail"].startswith(".../")


async def test_diagnostics_absent_without_session_factory() -> None:
    app = create_app()
    async with _client(app) as client:
        response = await client.get("/api/diagnostics")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "include_network", [True, False], ids=["network-on", "network-off"]
)
async def test_diagnostics_completes_within_local_budget(
    session_factory: async_sessionmaker, include_network: bool
) -> None:
    import time

    app = create_app(session_factory=session_factory)
    async with _client(app) as client:
        started = time.monotonic()
        response = await client.get(
            "/api/diagnostics", params={"include_network": include_network}
        )
        elapsed = time.monotonic() - started
    assert response.status_code == 200
    # Frozen §0.3 budgets: 15 s local, 30 s with network — CI headroom kept.
    assert elapsed < (28.0 if include_network else 13.0)


async def test_diagnostics_export_is_redacted_attachment(
    session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """P8-003: the export bundle carries no absolute paths or credential refs."""
    import json as _json

    secret_dir = str(tmp_path / "reports" / "deep")
    secret_cli = str(tmp_path / "tools" / "whisper-cli.exe")
    async with session_factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            report_directory=secret_dir,
            llm_provider="deepseek",
            llm_base_url="https://api.deepseek.com/v1",
            llm_model="deepseek-chat",
            llm_credential_ref="llm:deepseek",
            asr_provider="auto",
            whisper_cpp_executable=secret_cli,
            now=_NOW,
        )

    app = create_app(session_factory=session_factory, local_token=_TOKEN)
    headers = {"X-Local-Token": _TOKEN}
    async with _client(app) as client:
        denied = await client.get("/api/diagnostics/export")
        assert denied.status_code == 401
        r = await client.get("/api/diagnostics/export", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "evoblue-diagnostics-" in r.headers["content-disposition"]

    payload = _json.loads(r.text)
    assert payload["kind"] == "evoblue-diagnostics"
    assert payload["redacted"] is True
    assert payload["app_version"] == __version__
    assert [c["name"] for c in payload["checks"]] == list(CHECK_NAMES)
    settings_view = payload["settings"]
    assert settings_view["report_directory"] is not None
    assert secret_dir not in r.text, "absolute report path must be redacted"
    assert "whisper-cli" not in r.text, "CLI path must never appear"
    assert settings_view["whisper_cli_configured"] is True
    assert "llm:deepseek" not in r.text, "credential reference must be omitted"
    assert settings_view["llm_base_url_host"] == "api.deepseek.com"
    assert "index" in payload and "open_issues" in payload["index"]
