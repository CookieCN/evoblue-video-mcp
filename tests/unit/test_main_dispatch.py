"""P7-004: the engine entry's "bridge" subcommand dispatch.

The dispatch is the frozen bundle's bridge entry (no second PyInstaller EXE);
it must route to the MCP stdio entry before anything binds a port or writes
stdout.
"""

import socket
import sys

import evoblue_video_mcp.__main__ as entry
from evoblue_video_mcp.runtime import singleton


def test_bridge_subcommand_delegates_to_mcp_entry(monkeypatch) -> None:
    calls: list[str] = []
    fake = type("FakeMcpEntry", (), {"main": staticmethod(lambda: calls.append("x") or 0)})
    monkeypatch.setitem(sys.modules, "evoblue_video_mcp.mcp.__main__", fake)
    monkeypatch.setattr(sys, "argv", ["evoblue-engine-full", "bridge"])
    assert entry.main() == 0
    assert calls == ["x"]


def test_engine_path_still_runs_the_server(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    monkeypatch.setattr(sys, "argv", ["evoblue-engine-full"])

    def fake_create(settings):
        captured["ran"] = True

        class FakeApp:
            def mount(self, *a) -> None:
                raise AssertionError("no frontend expected")

        return FakeApp()

    monkeypatch.setattr(entry, "create_runtime_app", fake_create)
    monkeypatch.setattr(
        entry, "uvicorn", type("U", (), {"run": staticmethod(lambda *a, **k: None)})
    )
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(singleton, "acquire_mutex", lambda: object())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOBLUE_DATA_DIRECTORY", str(tmp_path / "data"))
    # never race the developer's real engine on the default port
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("EVOBLUE_ENGINE_PORT", str(free_port))

    assert entry.main() == 0
    assert captured["ran"] is True
