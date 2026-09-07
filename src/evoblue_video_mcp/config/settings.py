"""Safe Local Engine defaults; secrets are intentionally absent."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
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
    # EVOBLUE_LOCAL_TOKEN is the Bridge-launch-contract alias
    # (CLIENT_COMPATIBILITY.md). validation_alias names bypass env_prefix, so
    # both fully-qualified names are listed explicitly.
    local_access_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "local_access_token", "EVOBLUE_LOCAL_ACCESS_TOKEN", "EVOBLUE_LOCAL_TOKEN"
        ),
    )
    telemetry_enabled: bool = False
    data_directory: Path | None = None
    asr_model_dir: Path | None = None
    worker_owner: str = "local-worker"
    worker_lease_seconds: float = Field(default=60.0, gt=0)
    worker_idle_sleep: float = Field(default=1.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings object per process."""

    return Settings()
