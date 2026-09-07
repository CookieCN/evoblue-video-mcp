"""Render per-client MCP config fragments and verify the Bridge handshake.

P4 deliverable for docs/CLIENT_COMPATIBILITY.md: the four clients (Codex /
Claude / DeepSeek / WorkBuddy) all speak STDIO ``command``/``args``; only the
container file differs. Rendering is a pure function so the contract test can
lock the fragments against the doc's samples.

Since P5, both entry points delegate to the shared kernel
(``evoblue_video_mcp.application.client_config.render`` and
``evoblue_video_mcp.application.handshake``); the CLI surface and function
signatures are unchanged so the P4 contract test keeps passing untouched.

Usage:
    python scripts/verify_p4_clients.py render --client codex [--command PATH]
    python scripts/verify_p4_clients.py check-handshake [--port N] [--token T]

``check-handshake`` spawns the Bridge exactly like a client would (env-driven
config) and performs a real MCP initialize + list_tools against a running
Engine; exit code 0 means verified. Path/config guessing is out of scope (P5).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evoblue_video_mcp.application.client_config.models import EntryPayload
from evoblue_video_mcp.application.client_config.render import (
    render_json_fragment,
    render_toml_entry,
)

DEFAULT_COMMAND = r"C:\path\to\.venv\Scripts\python.exe"
DEFAULT_ARGS = ["-m", "evoblue_video_mcp.mcp"]
SERVER_NAME = "evoblue-video"
CLIENTS = ("codex", "claude", "deepseek", "workbuddy")


def render_client_config(
    client: str, *, command: str = DEFAULT_COMMAND, args: list[str] | None = None
) -> str:
    """Return the config fragment for one client (pure; no filesystem access).

    Delegates to the P5 renderer; the TOML basic strings still use the same
    escape set as JSON strings for the values emitted here, so ``json.dumps``
    yields a valid TOML string literal.
    """
    resolved = DEFAULT_ARGS if args is None else args
    payload = EntryPayload(command=command, args=tuple(resolved))
    if client == "codex":
        return render_toml_entry(SERVER_NAME, payload)
    if client in ("claude", "deepseek", "workbuddy"):
        return render_json_fragment(
            SERVER_NAME, payload, indent=None if client == "claude" else 2
        )
    raise ValueError(f"unknown client: {client}; expected one of {CLIENTS}")


def check_handshake(port: int | None, token: str | None) -> int:
    """Spawn the Bridge like a client would and run a real MCP handshake."""
    from evoblue_video_mcp.application.handshake import verify_bridge_handshake

    env = os.environ.copy()
    if port is not None:
        env["EVOBLUE_ENGINE_PORT"] = str(port)
    if token is not None:
        env["EVOBLUE_LOCAL_TOKEN"] = token
    env.setdefault("PYTHONUTF8", "1")
    result = asyncio.run(
        verify_bridge_handshake(sys.executable, DEFAULT_ARGS, env, server_name=SERVER_NAME)
    )
    detail = f"server={result.server_name} tools={list(result.tool_names)}"
    print(detail)
    if not result.ok:
        if result.stderr_tail:
            print(result.stderr_tail)
        print("HANDSHAKE FAILED")
        return 1
    print("HANDSHAKE OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    render = sub.add_parser("render", help="print one client's config fragment")
    render.add_argument("--client", required=True, choices=CLIENTS)
    render.add_argument("--command", default=DEFAULT_COMMAND)
    handshake = sub.add_parser("check-handshake", help="verify a real stdio handshake")
    handshake.add_argument("--port", type=int, default=None)
    handshake.add_argument("--token", default=None)
    args = parser.parse_args(argv)
    if args.action == "render":
        print(render_client_config(args.client, command=args.command))
        return 0
    return check_handshake(args.port, args.token)


if __name__ == "__main__":
    sys.exit(main())
