"""P4-002: engine-side diagnostics checks (docs/MCP_TOOLS.md §7).

Focus: frozen check names/order, single-check failure isolation (type name
only — never the exception message), credential redaction, and network gating.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp import __version__
from evoblue_video_mcp.application.diagnostics import (
    CHECK_NAMES,
    DiagnosticsReport,
    collect_diagnostics,
    redact_path,
)
from evoblue_video_mcp.asr import registry
from evoblue_video_mcp.storage import build_engine, init_db
from evoblue_video_mcp.storage.repository import save_app_settings

_NOW = 1_700_000_000.0


@dataclass
class _FakeProvider:
    provider_id: str


class FakeCredentialStore:
    def __init__(self, secret: str | None) -> None:
        self._secret = secret

    def get_secret(self, reference: str) -> str | None:
        return self._secret

    def set_secret(self, reference: str, secret: str) -> None:
        self._secret = secret

    def delete_secret(self, reference: str) -> None:
        self._secret = None


@pytest.fixture
async def make_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """One file-backed engine + session factory, owned and disposed here.

    A leaked engine leaves aiosqlite threads calling into a closed loop
    (PytestUnhandledThreadExceptionWarning gate) — see AGENTS.md Gotcha #5."""
    engine = build_engine(tmp_path / "diag.db")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _collect(
    factory: async_sessionmaker[AsyncSession], **kwargs: Any
) -> DiagnosticsReport:
    kwargs.setdefault("data_directory", Path.cwd())
    return await collect_diagnostics(factory, **kwargs)


def _by_name(report: DiagnosticsReport) -> dict[str, Any]:
    return {check.name: check for check in report.checks}


async def test_check_names_engine_version_and_overall_are_frozen(
    make_factory: async_sessionmaker[AsyncSession],
) -> None:
    report = await _collect(make_factory)
    assert [check.name for check in report.checks] == list(CHECK_NAMES)
    assert report.engine_version == __version__
    assert report.overall in {"pass", "warning"}


async def test_dev_defaults_degrade_without_failures(
    make_factory: async_sessionmaker[AsyncSession],
) -> None:
    report = await _collect(make_factory)
    named = _by_name(report)
    assert named["local_engine"].status == "pass"
    assert named["database"].status == "pass"
    assert named["cookie_browser"].status == "warning"
    assert named["asr_models"].status == "skipped"  # no model service wired
    assert named["llm_api"].status == "skipped"  # network opt-in only
    assert named["llm_config"].status == "warning"  # setup not completed
    assert not [check for check in report.checks if check.status == "fail"]


def test_redact_path_shows_only_the_tail() -> None:
    assert (
        redact_path(r"C:\Users\w\AppData\Local\EvoBlue\reports") == ".../EvoBlue/reports"
    )
    assert redact_path(None) is None


async def test_probe_exception_degrades_only_that_check(
    make_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def broken_probe() -> str | None:
        raise RuntimeError("C:\\secret\\path exploded")

    report = await _collect(
        make_factory, ffmpeg_version_probe=broken_probe
    )
    named = _by_name(report)
    assert named["ffmpeg"].status == "fail"
    assert named["ffmpeg"].message == "检查失败"
    assert named["ffmpeg"].detail == "RuntimeError"
    assert "C:" not in (named["ffmpeg"].detail or "")
    assert "secret" not in (named["ffmpeg"].message + (named["ffmpeg"].detail or ""))
    assert named["database"].status == "pass"  # isolation


async def test_llm_config_reports_key_state_without_leaking_secrets(
    make_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with make_factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            now=_NOW,
            llm_provider="deepseek",
            llm_base_url="https://api.deepseek.com",
            llm_model="deepseek-chat",
            llm_credential_ref="llm:deepseek",
        )

    configured = await _collect(
        make_factory, credential_store=FakeCredentialStore("sk-super-secret")
    )
    check = _by_name(configured)["llm_config"]
    assert check.status == "pass"
    assert "API Key 已配置" in check.message
    assert "sk-super-secret" not in check.message

    unconfigured = await _collect(
        make_factory, credential_store=FakeCredentialStore(None)
    )
    assert _by_name(unconfigured)["llm_config"].status == "warning"


@pytest.mark.parametrize(
    ("status_code", "expected_status", "message_fragment"),
    [
        (200, "pass", "可达"),
        (503, "warning", "HTTP 503"),
    ],
)
async def test_llm_api_network_probe_gated_and_graded(
    make_factory: async_sessionmaker[AsyncSession],
    status_code: int,
    expected_status: str,
    message_fragment: str,
) -> None:
    async with make_factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            now=_NOW,
            llm_provider="deepseek",
            llm_base_url="https://api.deepseek.com",
            llm_model="deepseek-chat",
        )

    async def fake_get(url: str) -> int:
        assert url == "https://api.deepseek.com/models"
        return status_code

    report = await _collect(
        make_factory, include_network=True, http_get=fake_get
    )
    check = _by_name(report)["llm_api"]
    assert check.status == expected_status
    assert message_fragment in check.message


async def test_llm_api_unreachable_degrades_to_fail_type_only(
    make_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with make_factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            now=_NOW,
            llm_provider="deepseek",
            llm_base_url="https://api.deepseek.com",
            llm_model="deepseek-chat",
        )

    async def refusing(url: str) -> int:
        raise RuntimeError("connection refused to 127.0.0.1:9999")

    report = await _collect(make_factory, include_network=True, http_get=refusing)
    check = _by_name(report)["llm_api"]
    assert check.status == "fail"
    assert check.detail == "RuntimeError"
    assert "127.0.0.1" not in (check.detail or "")


async def test_asr_runtime_reflects_provider_registry(
    make_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry.clear()
    try:
        empty = await _collect(make_factory)
        assert _by_name(empty)["asr_runtime"].status in {"warning"}

        registry.register_provider(_FakeProvider("fake-asr"))
        registered = await _collect(make_factory)
        check = _by_name(registered)["asr_runtime"]
        assert check.status == "pass"
        assert "fake-asr" in check.message
    finally:
        registry.clear()
