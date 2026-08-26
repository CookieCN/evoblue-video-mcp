"""Safe Local Engine defaults; secrets are intentionally absent."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Development/runtime settings that never contain provider credentials."""

    model_config = SettingsConfigDict(env_prefix="EVOBLUE_", extra="ignore")

    app_name: str = "EvoBlue Video MCP"
    environment: Literal["development", "test", "production"] = "development"
    engine_host: Literal["127.0.0.1"] = "127.0.0.1"
    engine_port: int = Field(default=8765, ge=1024, le=65535)
    # Random local access token protecting data endpoints; empty means auth is disabled
    # (development only). Production generates one at engine startup.
    local_access_token: str = ""
    telemetry_enabled: bool = False
    data_directory: Path | None = None
    worker_owner: str = "local-worker"
    worker_lease_seconds: float = Field(default=60.0, gt=0)
    worker_idle_sleep: float = Field(default=1.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings object per process."""

    return Settings()
