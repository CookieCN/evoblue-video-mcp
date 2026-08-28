"""Production entrypoint: start the Local Engine HTTP server.

Launches uvicorn bound to the loopback address from :class:`Settings`. In
production environments a random local access token is generated on first run
and persisted inside the engine's data directory (mode 0600 on POSIX), so the
WebUI and local clients can authenticate without any secret ever shipping with
the package. When a built WebUI (``frontend/dist``) sits next to the
executable — as it does in packaged onedir builds — it is served at ``/``;
API routes keep precedence.
"""

import os
import secrets
import sys
from pathlib import Path

import uvicorn

from evoblue_video_mcp.config.settings import Settings
from evoblue_video_mcp.runtime.bootstrap import create_runtime_app, resolve_runtime_paths

_TOKEN_FILE = "local_token"


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


def _mount_frontend(app: object) -> None:
    """Serve the built WebUI when bundled (packaged builds) or built locally.

    Packaged onedir builds carry ``webui/`` next to the executable; source
    checkouts can serve a locally built ``frontend/dist``. Absent in both
    places (or behind the Vite dev server) nothing is mounted — API routes
    always keep precedence because they register first.
    """
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "webui")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "webui")
    candidates.append(Path.cwd() / "frontend" / "dist")

    dist = next((path for path in candidates if path.is_dir()), None)
    if dist is None:
        return
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(dist), html=True), name="webui")  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> int:
    del argv  # settings come from EVOBLUE_* environment variables only
    settings = Settings()
    paths = resolve_runtime_paths(settings)
    paths.data.mkdir(parents=True, exist_ok=True)

    if settings.environment == "production" and not settings.local_access_token:
        settings = settings.model_copy(
            update={"local_access_token": _persisted_local_token(settings, paths.data)}
        )

    app = create_runtime_app(settings)
    _mount_frontend(app)
    uvicorn.run(
        app,
        host=settings.engine_host,
        port=settings.engine_port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
