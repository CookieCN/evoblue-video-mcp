"""Local Engine FastAPI factory."""

from typing import Literal, TypedDict

from fastapi import FastAPI

from evoblue_video_mcp import __version__
from evoblue_video_mcp.config import Settings


class HealthResponse(TypedDict):
    status: Literal["ok"]
    service: str
    version: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the Local Engine HTTP application without starting a server."""

    app_settings = settings or Settings()
    app = FastAPI(title=app_settings.app_name, version=__version__)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return {"status": "ok", "service": app_settings.app_name, "version": __version__}

    return app

