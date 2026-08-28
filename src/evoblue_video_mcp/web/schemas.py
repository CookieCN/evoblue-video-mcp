"""Pydantic models for the Local Engine HTTP API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


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
    asr_provider_id: str | None = None
    asr_model_id: str | None = None
    asr_model_version: str | None = None
    asr_recommendation_model_id: str | None = None


class AppSettingsResponse(StrictModel):
    setup_completed: bool
    report_directory: str | None = None
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key_configured: bool = False
    asr_provider: str = "auto"
    whisper_cpp_executable: str | None = None


class AppSettingsUpdate(StrictModel):
    setup_completed: bool | None = None
    report_directory: str | None = None
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    asr_provider: Literal[
        "auto",
        "sherpa-onnx-lite",
        "sherpa-onnx-standard",
        "whisper-cpp-base",
    ] | None = None
    whisper_cpp_executable: str | None = None

    @field_validator("llm_base_url")
    @classmethod
    def validate_llm_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname:
            return value.rstrip("/")
        if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            return value.rstrip("/")
        raise ValueError("base URL must use HTTPS, except for an explicit local endpoint")


class SubmitJobInput(StrictModel):
    url: str
    mode: Literal["auto", "standard", "unboxing"] = "auto"
    asr: Literal["auto", "disabled", "required"] = "auto"
    language: str | None = None


class SubmitJobResponse(StrictModel):
    job_id: str
    status: str
    reused: bool


class ModelSummaryResponse(StrictModel):
    model_id: str
    version: str
    tier: str
    provider: str
    languages: list[str]
    compressed_size_bytes: int = Field(gt=0)
    installed_size_bytes: int = Field(gt=0)
    license: str
    attribution: str
    redistribution: str
    installed: bool
    active: bool
    installed_path: str | None = None
    status: str | None = None
    downloaded_bytes: int = Field(ge=0)
    error_code: str | None = None
    formal_default: bool


class ModelListResponse(StrictModel):
    items: list[ModelSummaryResponse]


class UninstallResponse(StrictModel):
    model_id: str
    reclaimed_bytes: int = Field(ge=0)
    pending_reclaim_bytes: int = Field(ge=0)
