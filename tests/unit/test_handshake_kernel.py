"""P5-002 unit tests: handshake kernel redaction, error mapping, no leaks.

The real-wire success/failure predicates are covered in
``tests/integration/test_client_config_handshake.py`` against a live Engine;
here the cheap deterministic paths are pinned (contract §6/§8).
"""

import os
import sys

from evoblue_video_mcp.application.handshake import (
    redact_stderr,
    verify_bridge_handshake,
)


def test_redact_stderr_masks_credentials_and_shrinks_paths() -> None:
    text = (
        "starting bridge\n"
        "EVOBLUE_LOCAL_TOKEN=supersecret-value\n"
        "api_key: sk-abcdef123456\n"
        "loading from C:\\Users\\someone\\AppData\\Local\\EvoBlue\\cache.bin ok\n"
        "reading /home/somebody/.config/evoblue/x.conf done\n"
    )
    redacted = redact_stderr(text)
    assert redacted is not None
    assert "supersecret-value" not in redacted
    assert "sk-abcdef123456" not in redacted
    assert "<redacted>" in redacted
    assert "someone" not in redacted
    assert "somebody" not in redacted
    assert "cache.bin" in redacted  # the tail survives, the location does not
    assert "x.conf" in redacted


def test_redact_stderr_caps_length_and_handles_empty() -> None:
    assert redact_stderr(None) is None
    assert redact_stderr("   \n") is None
    capped = redact_stderr("x" * 5000, tail_chars=100)
    assert capped is not None
    assert len(capped) <= 101


def test_spawn_failure_maps_to_spawn_failed() -> None:
    missing = (
        r"C:\definitely\not\a\real\python.exe"
        if os.name == "nt"
        else "/definitely/not/a/real/python"
    )
    result = _run(missing, ["-c", "pass"])
    assert result.ok is False
    assert result.error == "spawn_failed"
    assert result.server_name is None


def test_hung_child_maps_to_timeout_within_budget() -> None:
    result = _run(sys.executable, ["-c", "import time; time.sleep(120)"], timeout_s=3.0)
    assert result.ok is False
    assert result.error == "timeout"
    assert result.elapsed_s < 10.0


def _run(command: str, args: list[str], *, timeout_s: float = 45.0):
    import asyncio

    env = os.environ.copy()
    env["EVOBLUE_ENGINE_PORT"] = str(59999)  # nothing listens; handshake ignores it
    env.setdefault("PYTHONUTF8", "1")
    return asyncio.run(
        verify_bridge_handshake(command, args, env, timeout_s=timeout_s)
    )
