"""F1: POST /api/settings/test — probe the on-screen config, never persist.

Contract: docs/CONFIGURATION.md §LLM 连接测试. The endpoint answers with the
frozen status enum and MUST NOT touch the database or the credential store;
the payload's own key is preferred, and a stored key is reused only when the
provider is unchanged (the same guard the save gate applies).
"""

import time
from collections.abc import Awaitable, Callable, Mapping

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import evoblue_video_mcp.web.app as web_app
from evoblue_video_mcp.storage.repository import get_app_settings, save_app_settings
from evoblue_video_mcp.web.app import create_app

_TOKEN = "test-conn-token"
_NOW = 1_700_000_000.0

ProbeFactory = Callable[[], Callable[[str, Mapping[str, str]], Awaitable[int]]]


class _SpyStore:
    """Credential store double that records writes and never touches a vault."""

    def __init__(self, secret: str | None) -> None:
        self.secret = secret
        self.writes: list[tuple[str, str]] = []
        self.deletes: list[str] = []

    def get_secret(self, reference: str) -> str | None:
        return self.secret

    def set_secret(self, reference: str, secret: str) -> None:
        self.writes.append((reference, secret))

    def delete_secret(self, reference: str) -> None:
        self.deletes.append(reference)


def _client(app: object) -> httpx.AsyncClient:  # type: ignore[type-arg]
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _seed_configured(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as sess:
        await save_app_settings(
            sess,
            setup_completed=True,
            now=_NOW,
            llm_provider="deepseek",
            llm_base_url="https://api.deepseek.com",
            llm_model="deepseek-chat",
            llm_credential_ref="llm:deepseek",
            llm_credential_origin="https://api.deepseek.com",
        )


def _install_probe(monkeypatch: pytest.MonkeyPatch, status: int) -> dict[str, object]:
    seen: dict[str, object] = {}

    async def probe(url: str, headers: Mapping[str, str]) -> int:
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        return status

    monkeypatch.setattr(web_app, "_llm_status_probe", lambda: probe)
    return seen


async def test_settings_test_requires_local_token(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=session_factory, local_token=_TOKEN)
    async with _client(app) as client:
        denied = await client.post("/api/settings/test", json={})
        assert denied.status_code == 401


async def test_settings_test_uses_payload_key_and_never_persists(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_configured(session_factory)
    seen = _install_probe(monkeypatch, 200)
    store = _SpyStore("sk-stored-old-key")
    app = create_app(
        session_factory=session_factory, local_token=_TOKEN, credential_store=store
    )
    async with _client(app) as client:
        r = await client.post(
            "/api/settings/test",
            headers={"X-Local-Token": _TOKEN},
            json={
                "llm_provider": "deepseek",
                "llm_base_url": "https://api.deepseek.com",
                "llm_model": "deepseek-flash",
                "llm_api_key": "sk-from-form",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert seen["url"] == "https://api.deepseek.com/models"
    assert seen["auth"] == "Bearer sk-from-form"
    # Probe-only: no keyring write, no database change.
    assert store.writes == [] and store.deletes == []
    async with session_factory() as sess:
        row = await get_app_settings(sess)
    assert row is not None
    assert row.llm_model == "deepseek-chat"
    assert row.llm_credential_ref == "llm:deepseek"
    assert "sk-from-form" not in r.text and "sk-stored-old-key" not in r.text


async def test_settings_test_empty_key_reuses_stored_for_same_provider(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_configured(session_factory)
    seen = _install_probe(monkeypatch, 401)
    store = _SpyStore("sk-stored-old-key")
    app = create_app(
        session_factory=session_factory, local_token=_TOKEN, credential_store=store
    )
    async with _client(app) as client:
        r = await client.post(
            "/api/settings/test",
            headers={"X-Local-Token": _TOKEN},
            json={"llm_provider": "deepseek"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "auth_failed"
    assert seen["auth"] == "Bearer sk-stored-old-key"


async def test_settings_test_provider_switch_never_uses_old_credential(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_configured(session_factory)
    seen = _install_probe(monkeypatch, 200)
    store = _SpyStore("sk-stored-old-key")
    app = create_app(
        session_factory=session_factory, local_token=_TOKEN, credential_store=store
    )
    async with _client(app) as client:
        r = await client.post(
            "/api/settings/test",
            headers={"X-Local-Token": _TOKEN},
            json={"llm_provider": "openai"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "not_configured"
    assert seen == {}  # the probe must not even run


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(200, "ok"), (403, "auth_failed"), (500, "http_error")],
)
async def test_settings_test_grades_response_status(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected: str,
) -> None:
    await _seed_configured(session_factory)
    _install_probe(monkeypatch, status_code)
    app = create_app(
        session_factory=session_factory,
        local_token=_TOKEN,
        credential_store=_SpyStore("sk-stored-old-key"),
    )
    async with _client(app) as client:
        r = await client.post(
            "/api/settings/test",
            headers={"X-Local-Token": _TOKEN},
            json={"llm_api_key": "sk-from-form"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == expected


async def test_settings_test_network_error_and_unconfigured(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_configured(session_factory)

    async def probe(url: str, headers: Mapping[str, str]) -> int:
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(web_app, "_llm_status_probe", lambda: probe)
    app = create_app(
        session_factory=session_factory,
        local_token=_TOKEN,
        credential_store=_SpyStore("sk-stored-old-key"),
    )
    async with _client(app) as client:
        r = await client.post(
            "/api/settings/test",
            headers={"X-Local-Token": _TOKEN},
            json={"llm_api_key": "sk-from-form"},
        )
        assert r.json()["status"] == "network_error"

        # no usable key (fresh store, empty payload): friendly not_configured
        blank_app = create_app(
            session_factory=session_factory,
            local_token=_TOKEN,
            credential_store=_SpyStore(None),
        )
    async with _client(blank_app) as client2:
        blank = await client2.post(
            "/api/settings/test", headers={"X-Local-Token": _TOKEN}, json={}
        )
    assert blank.status_code == 200
    assert blank.json()["status"] == "not_configured"


async def test_settings_test_rejects_plain_http_remote_base_url(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=session_factory, local_token=_TOKEN)
    async with _client(app) as client:
        r = await client.post(
            "/api/settings/test",
            headers={"X-Local-Token": _TOKEN},
            json={
                "llm_base_url": "http://api.example.com",
                "llm_api_key": "sk-x",
            },
        )
    assert r.status_code == 422


async def test_settings_keyring_read_is_bounded(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked OS credential store must not hang the settings API (#12)."""

    class HangingStore:
        def get_secret(self, reference: str) -> str | None:
            time.sleep(5.0)
            return None

        def set_secret(self, reference: str, secret: str) -> None:
            raise AssertionError("not used in this test")

        def delete_secret(self, reference: str) -> None:
            raise AssertionError("not used in this test")

    monkeypatch.setattr(web_app, "_KEYRING_TIMEOUT_S", 0.05)
    app = create_app(
        session_factory=session_factory,
        local_token=_TOKEN,
        credential_store=HangingStore(),
    )
    async with _client(app) as client:
        started = time.monotonic()
        r = await client.get("/api/settings", headers={"X-Local-Token": _TOKEN})
        elapsed = time.monotonic() - started
    assert r.status_code == 200
    assert r.json()["llm_api_key_configured"] is False
    assert elapsed < 2.0, "a blocked keyring read must time out, not hang"


async def test_settings_test_keyring_error(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenStore:
        def get_secret(self, reference: str) -> str | None:
            raise RuntimeError("vault locked")

        def set_secret(self, reference: str, secret: str) -> None:
            raise AssertionError("not used")

        def delete_secret(self, reference: str) -> None:
            raise AssertionError("not used")

    await _seed_configured(session_factory)
    _install_probe(monkeypatch, 200)
    app = create_app(
        session_factory=session_factory,
        local_token=_TOKEN,
        credential_store=BrokenStore(),
    )
    async with _client(app) as client:
        r = await client.post(
            "/api/settings/test",
            headers={"X-Local-Token": _TOKEN},
            json={"llm_provider": "deepseek"},
        )
    assert r.json()["status"] == "keyring_error"


async def test_settings_test_reuse_rejects_changed_origin(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1 (review): the stored credential belongs to the endpoint it was saved
    for. Same provider + edited Base URL must NOT ship the old key to the new
    site — reuse requires a normalized-origin match, and on mismatch the probe
    never fires."""
    await _seed_configured(session_factory)
    seen = _install_probe(monkeypatch, 200)
    store = _SpyStore("sk-stored-old-key")
    app = create_app(
        session_factory=session_factory, local_token=_TOKEN, credential_store=store
    )
    async with _client(app) as client:
        r = await client.post(
            "/api/settings/test",
            headers={"X-Local-Token": _TOKEN},
            json={
                "llm_provider": "deepseek",
                "llm_base_url": "https://different.example",
            },
        )
    assert r.status_code == 200
    assert r.json()["status"] == "not_configured"
    assert "url" not in seen, "probe must not fire for a cross-origin reuse"
    assert "sk-stored-old-key" not in r.text


async def test_settings_test_uppercase_host_reuses_stored_key(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: DNS hostnames are case-insensitive — an uppercase host is the same
    origin and still reuses the stored key."""
    await _seed_configured(session_factory)
    seen = _install_probe(monkeypatch, 200)
    store = _SpyStore("sk-stored-old-key")
    app = create_app(
        session_factory=session_factory, local_token=_TOKEN, credential_store=store
    )
    async with _client(app) as client:
        r = await client.post(
            "/api/settings/test",
            headers={"X-Local-Token": _TOKEN},
            json={"llm_provider": "deepseek", "llm_base_url": "https://API.DEEPSEEK.COM"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert seen["auth"] == "Bearer sk-stored-old-key"


async def test_settings_test_same_origin_different_path_reuses_stored_key(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: the credential scope is the ORIGIN, not the exact URL — a different
    path on the same scheme/host/port still reuses the stored key."""
    await _seed_configured(session_factory)
    seen = _install_probe(monkeypatch, 200)
    store = _SpyStore("sk-stored-old-key")
    app = create_app(
        session_factory=session_factory, local_token=_TOKEN, credential_store=store
    )
    async with _client(app) as client:
        r = await client.post(
            "/api/settings/test",
            headers={"X-Local-Token": _TOKEN},
            json={
                "llm_provider": "deepseek",
                "llm_base_url": "https://api.deepseek.com/v1",
            },
        )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert seen["url"] == "https://api.deepseek.com/v1/models"
    assert seen["auth"] == "Bearer sk-stored-old-key"


async def test_settings_test_port_change_rejects_reuse(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: an effective-port change is an origin change too. (A public
    http:// target cannot even pass the schema's HTTPS gate, so scheme
    changes are already structurally out of scope.)"""
    await _seed_configured(session_factory)
    seen = _install_probe(monkeypatch, 200)
    store = _SpyStore("sk-stored-old-key")
    app = create_app(
        session_factory=session_factory, local_token=_TOKEN, credential_store=store
    )
    async with _client(app) as client:
        for changed in ("https://api.deepseek.com:8443",):
            r = await client.post(
                "/api/settings/test",
                headers={"X-Local-Token": _TOKEN},
                json={"llm_provider": "deepseek", "llm_base_url": changed},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "not_configured", changed
    assert "url" not in seen


async def test_settings_test_cross_origin_with_own_key_uses_own_key(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: an explicitly provided key is used for whatever endpoint the user
    typed — the boundary only gates silent reuse of the stored one."""
    await _seed_configured(session_factory)
    seen = _install_probe(monkeypatch, 200)
    store = _SpyStore("sk-stored-old-key")
    app = create_app(
        session_factory=session_factory, local_token=_TOKEN, credential_store=store
    )
    async with _client(app) as client:
        r = await client.post(
            "/api/settings/test",
            headers={"X-Local-Token": _TOKEN},
            json={
                "llm_provider": "deepseek",
                "llm_base_url": "https://different.example",
                "llm_api_key": "sk-explicit-for-new-site",
            },
        )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert seen["auth"] == "Bearer sk-explicit-for-new-site"
