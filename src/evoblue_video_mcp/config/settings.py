"""Safe Local Engine defaults; secrets are intentionally absent."""

from functools import lru_cache
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
    telemetry_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings object per process."""

    return Settings()

