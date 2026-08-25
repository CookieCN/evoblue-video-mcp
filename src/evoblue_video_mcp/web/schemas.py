"""Pydantic models for the Local Engine HTTP API."""

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobListItem(StrictModel):
    job_id: str
    status: str
    stage: str | None = None
    progress: int = Field(ge=0)
    error_code: str | None = None
    created_at: float


class JobListResponse(StrictModel):
    items: list[JobListItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class JobDetailResponse(StrictModel):
    job_id: str
    status: str
    stage: str | None = None
    progress: int = Field(ge=0)
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool
    created_at: float
    updated_at: float


class AppSettingsResponse(StrictModel):
    setup_completed: bool
    report_directory: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None


class AppSettingsUpdate(StrictModel):
    setup_completed: bool | None = None
    report_directory: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
