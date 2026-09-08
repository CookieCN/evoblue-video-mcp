"""Production entrypoint: start the Local Engine HTTP server.

Launches uvicorn bound to the loopback address from :class:`Settings`. In
production a random local access token is generated on first run and persisted
inside the engine's data directory (mode 0600 on POSIX), so the WebUI and local
clients can authenticate without any secret ever shipping with the package.

Packaged builds (P7, INSTALLER_RELEASE_CONTRACT):
  - a frozen process runs with environment "production" unless the user
    explicitly set EVOBLUE_ENVIRONMENT (a packaged product must never run
    unauthenticated);
  - on production boot with a bundled WebUI the engine opens the default
    browser at /#evoblue_token=<token> (EVOBLUE_OPEN_UI=0 opts out). The token
    travels in the URL *fragment* - never the query string - because the
    uvicorn access log records full query strings;
  - a SPA catch-all keeps deep links (/settings, /mcp, ...) working in the
    packaged WebUI;
  - "<engine-exe> bridge" runs the STDIO MCP bridge (frozen client-config
    payload, contract section 5).
"""

import os
import secrets
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI

import evoblue_video_mcp
from evoblue_video_mcp.config.settings import Settings
from evoblue_video_mcp.runtime import singleton
from evoblue_video_mcp.runtime.bootstrap import create_runtime_app, resolve_runtime_paths

_TOKEN_FILE = "local_token"
_FRAGMENT_KEY = "evoblue_token"
_OPEN_UI_ENV = "EVOBLUE_OPEN_UI"
_ENVIRONMENT_ENV = "EVOBLUE_ENVIRONMENT"


def _env_explicitly_set(name: str) -> bool:
    """Case-insensitive env presence check (pydantic-settings matches that way)."""
    return any(key.upper() == name for key in os.environ)


def _persisted_local_token(settings: Settings, data_dir: Path) -> str:
    """Return the data-directory local token, creating one on first run."""
    token_file = data_dir / _TOKEN_FILE
    if token_file.is_file():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    token_file.write_text(token, encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(token_file, 0o600)
    return token


def _dist_root() -> Path | None:
    """Locate the built WebUI: packaged webui/ next to the exe, else frontend/dist."""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "webui")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "webui")
    candidates.append(Path.cwd() / "frontend" / "dist")
    return next((path for path in candidates if path.is_dir()), None)


def _install_spa_catchall(app: FastAPI, dist: Path) -> None:
    """Register GET catch-all that serves deep links from index.html.

    Registered AFTER every API route (the caller invokes this post-create_app),
    so /api/* keeps answering first; a path that resolves to a real file under
    the dist root is served as-is (assets), everything else non-API returns
    index.html for React Router to handle.
    """
    from fastapi.responses import FileResponse

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_catchall(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404)
        root = dist.resolve()
        candidate = (dist / path).resolve()
        if path and _is_relative_to(candidate, root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(root / "index.html")


def _is_relative_to(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _mount_frontend(app: FastAPI) -> Path | None:
    """Serve the built WebUI when bundled (packaged builds) or built locally.

    Packaged onedir builds carry webui/ next to the executable; source
    checkouts can serve a locally built frontend/dist. Absent in both places
    (or behind the Vite dev server) nothing is mounted - API routes always
    keep precedence because they register first. Returns the dist root so the
    caller can open the UI with the token fragment.
    """
    dist = _dist_root()
    if dist is None:
        return None
    _install_spa_catchall(app, dist)
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(dist), html=True), name="webui")
    return dist


def _open_ui_url(settings: Settings, token: str | None) -> str:
    """Loopback UI URL; production carries the token in the URL fragment."""
    base = f"http://{settings.engine_host}:{settings.engine_port}/"
    if settings.environment == "production" and token:
        return f"{base}#{_FRAGMENT_KEY}={token}"
    return base


def _maybe_open_ui(
    settings: Settings, dist: Path | None, token: str | None, *, delay_s: float = 1.0
) -> threading.Timer | None:
    """Open the default browser once the server is about to accept requests.

    Production + bundled WebUI only; EVOBLUE_OPEN_UI=0 (or false/no/off) opts
    out. Delayed on a daemon timer so uvicorn can bind while the browser opens.
    Returns the timer so tests can join it.
    """
    if settings.environment != "production" or dist is None:
        return None
    if os.environ.get(_OPEN_UI_ENV, "").strip().lower() in {"0", "false", "no", "off"}:
        return None
    timer = threading.Timer(
        delay_s, webbrowser.open, args=(_open_ui_url(settings, token),)
    )
    timer.daemon = True
    timer.start()
    return timer


def main(argv: list[str] | None = None) -> int:
    """Run the Local Engine, or the STDIO bridge via the "bridge" subcommand."""
    del argv  # settings come from EVOBLUE_* environment variables only
    if sys.argv[1:2] == ["bridge"]:
        # import_module consults sys.modules first: a parent-package attribute
        # for "__main__" (set by an earlier import) must not shadow the lookup
        import importlib

        mcp_entry = importlib.import_module("evoblue_video_mcp.mcp.__main__")
        return int(mcp_entry.main())

    settings = Settings()
    # P7 frozen=>production rule (contract section 4): a packaged process must
    # never run unauthenticated. Detection is env-based (not Settings default)
    # so a user can still opt into a custom environment for packaged builds.
    if getattr(sys, "frozen", False) and not _env_explicitly_set(_ENVIRONMENT_ENV):
        settings = settings.model_copy(update={"environment": "production"})

    paths = resolve_runtime_paths(settings)
    paths.data.mkdir(parents=True, exist_ok=True)

    # P7 single-instance guard (contract section 3): mutex -> lock file ->
    # port probe, each with a stable exit code and a Chinese message instead
    # of a traceback. The port bind remains the final authority.
    conflict = singleton.acquire(
        paths.data,
        host=settings.engine_host,
        port=settings.engine_port,
        version=evoblue_video_mcp.__version__,
    )
    if conflict is not None:
        print(
            singleton.conflict_message(conflict, settings.engine_port), file=sys.stderr
        )
        return conflict

    # An explicitly-provided EVOBLUE_LOCAL_TOKEN must reach the browser
    # bootstrap too (P2 review): the fragment URL is built from this value,
    # so it starts as the settings value and is only replaced when a token
    # had to be generated/persisted here.
    token: str | None = settings.local_access_token or None
    if settings.environment == "production" and not token:
        token = _persisted_local_token(settings, paths.data)
        settings = settings.model_copy(update={"local_access_token": token})

    app = create_runtime_app(settings)
    dist = _mount_frontend(app)
    _maybe_open_ui(settings, dist, token)
    uvicorn.run(
        app,
        host=settings.engine_host,
        port=settings.engine_port,
        log_level="info",
    )
    singleton.release_lock(paths.data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
