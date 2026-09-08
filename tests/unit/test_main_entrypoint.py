"""P7-002/004 entrypoint rules: frozen=>production, bridge dispatch, open-UI URL.

The frozen rule is enforced in main() (never in Settings defaults, which would
change behaviour for every test-suite Settings()). The open-UI URL must carry
the token in the fragment and honour EVOBLUE_OPEN_UI.
"""

import socket
import sys

import evoblue_video_mcp.__main__ as entry
from evoblue_video_mcp.config.settings import Settings
from evoblue_video_mcp.runtime import singleton


def _free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_open_ui_url_carries_token_in_fragment(monkeypatch) -> None:
    from pathlib import Path

    settings = Settings(
        environment="production",
        local_access_token="tok",
        data_directory=Path("."),  # unused by the URL builder
    )
    url = entry._open_ui_url(settings, "tok")
    assert url.endswith("/#evoblue_token=tok")
    assert "?" not in url, "token must never travel in the query string"


def test_open_ui_url_omits_token_outside_production() -> None:
    from pathlib import Path

    settings = Settings(environment="development", data_directory=Path("."))
    assert "#" not in entry._open_ui_url(settings, "tok")


def test_maybe_open_ui_disabled_by_env(monkeypatch) -> None:
    from pathlib import Path

    opened: list[str] = []
    monkeypatch.setattr(entry.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setenv("EVOBLUE_OPEN_UI", "0")
    settings = Settings(environment="production", data_directory=Path("."))
    timer = entry._maybe_open_ui(settings, Path("."), "tok", delay_s=0.0)
    assert timer is None
    assert opened == []


def test_maybe_open_ui_opens_with_fragment(monkeypatch) -> None:
    from pathlib import Path

    opened: list[str] = []
    monkeypatch.setattr(entry.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.delenv("EVOBLUE_OPEN_UI", raising=False)
    settings = Settings(
        environment="production", engine_port=8765, data_directory=Path(".")
    )
    timer = entry._maybe_open_ui(settings, Path("."), "tok", delay_s=0.0)
    assert timer is not None
    timer.join(timeout=5)
    assert opened == ["http://127.0.0.1:8765/#evoblue_token=tok"]


def test_main_dispatches_bridge_subcommand(monkeypatch) -> None:
    calls: list[str] = []
    bridge = type("B", (), {"main": staticmethod(lambda: calls.append("bridge") or 7)})
    monkeypatch.setitem(sys.modules, "evoblue_video_mcp.mcp.__main__", bridge)
    monkeypatch.setattr(sys, "argv", ["evoblue-engine-full", "bridge"])
    assert entry.main() == 7
    assert calls == ["bridge"]


def test_main_frozen_defaults_to_production(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    monkeypatch.setattr(sys, "argv", ["evoblue-engine-full"])

    def fake_create(settings):
        captured["environment"] = settings.environment
        captured["token"] = settings.local_access_token

        class FakeApp:
            def mount(self, *a) -> None:  # pragma: no cover - not reached
                raise AssertionError("no frontend expected in this test")

        return FakeApp()

    monkeypatch.setattr(entry, "create_runtime_app", fake_create)
    monkeypatch.setattr(
        entry, "uvicorn", type("U", (), {"run": staticmethod(lambda *a, **k: None)})
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(singleton, "acquire_mutex", lambda: object())
    monkeypatch.chdir(tmp_path)  # keep frontend/dist out of _dist_root
    monkeypatch.setenv("EVOBLUE_DATA_DIRECTORY", str(tmp_path / "data"))
    monkeypatch.setenv("EVOBLUE_ENGINE_PORT", str(_free_port()))
    monkeypatch.delenv("EVOBLUE_ENVIRONMENT", raising=False)
    monkeypatch.delenv("EVOBLUE_LOCAL_TOKEN", raising=False)
    monkeypatch.delenv("EVOBLUE_LOCAL_ACCESS_TOKEN", raising=False)

    assert entry.main() == 0
    assert captured["environment"] == "production"
    assert captured["token"], "production boot must persist a local token"


def test_main_frozen_respects_explicit_environment(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    monkeypatch.setattr(sys, "argv", ["evoblue-engine-full"])

    def fake_create(settings):
        captured["environment"] = settings.environment
        captured["token"] = settings.local_access_token

        class FakeApp:
            def mount(self, *a) -> None:  # pragma: no cover - not reached
                raise AssertionError("no frontend expected in this test")

        return FakeApp()

    monkeypatch.setattr(entry, "create_runtime_app", fake_create)
    monkeypatch.setattr(
        entry, "uvicorn", type("U", (), {"run": staticmethod(lambda *a, **k: None)})
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(singleton, "acquire_mutex", lambda: object())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOBLUE_DATA_DIRECTORY", str(tmp_path / "data"))
    monkeypatch.setenv("EVOBLUE_ENGINE_PORT", str(_free_port()))
    monkeypatch.setenv("EVOBLUE_ENVIRONMENT", "development")

    assert entry.main() == 0
    assert captured["environment"] == "development"
