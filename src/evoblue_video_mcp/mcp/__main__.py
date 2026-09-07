"""STDIO entry point for the EvoBlue MCP Bridge.

Launched by MCP clients as ``command=<python> args=["-m", "evoblue_video_mcp.mcp"]``
(docs/CLIENT_COMPATIBILITY.md Bridge 启动合同). stdout carries only MCP
protocol — every log line goes to stderr (SYSTEM_BOUNDARIES). Settings come
from ``EVOBLUE_*`` environment variables only, same discipline as the Engine
entry. The local token is discovered read-only (env → data-directory token
file); the Bridge never generates or writes a token (ADR 0004).
"""

import asyncio
import logging
import sys
from pathlib import Path

from evoblue_video_mcp.config import Settings
from evoblue_video_mcp.mcp.engine_client import EngineClient
from evoblue_video_mcp.mcp.server import build_bridge_server
from evoblue_video_mcp.runtime.bootstrap import resolve_runtime_paths

_TOKEN_FILENAME = "local_token"


def _configure_stderr_logging() -> None:
    """Route all logging to stderr; stdout stays protocol-only."""
    root = logging.getLogger()
    if any(
        isinstance(handler, logging.StreamHandler)
        and getattr(handler.stream, "name", None) == "<stderr>"
        for handler in root.handlers
    ):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def discover_local_token(settings: Settings, data_dir: Path) -> str | None:
    """Find the Engine's local token without ever creating one.

    Order: explicit env token → the Engine's persisted ``local_token`` file →
    None (a development Engine may run without auth; a production mismatch
    then surfaces as ``ENGINE_UNAUTHORIZED`` with repair guidance).
    """
    if settings.local_access_token:
        return settings.local_access_token
    try:
        token = (data_dir / _TOKEN_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


async def _serve(client: EngineClient, server: object) -> None:
    """Own one event loop for the stdio server and the Engine client."""
    try:
        # Serves until the MCP client closes stdin.
        await server.run_stdio_async()  # type: ignore[attr-defined]
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    """Run the Bridge over stdio until the client closes the stream."""
    del argv  # settings come from EVOBLUE_* environment variables only
    _configure_stderr_logging()
    settings = Settings()
    data_dir = resolve_runtime_paths(settings).data
    token = discover_local_token(settings, data_dir)
    logging.getLogger(__name__).info(
        "starting bridge for %s (token %s)",
        f"{settings.engine_host}:{settings.engine_port}",
        "configured" if token else "absent",
    )
    client = EngineClient.from_settings(settings, token=token)
    server = build_bridge_server(client)
    asyncio.run(_serve(client, server))
    return 0


if __name__ == "__main__":
    sys.exit(main())
