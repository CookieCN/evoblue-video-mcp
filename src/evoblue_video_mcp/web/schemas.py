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
    # F3 (feedback #9): identity lands with metadata; url is the fallback
    # identifier while metadata has not been fetched yet.
    title: str | None = None
    platform: str | None = None
    url: str | None = None


class JobListResponse(StrictModel):
    items: list[JobListItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class UnifiedJobListItem(StrictModel):
    """One row of the default merge view, tagged with its segment.

    ``kind='job'`` rows carry the live job fields (status/error_code/url);
    ``kind='history'`` rows are indexed reports presented as completed.
    """

    kind: Literal["job", "history"]
    job_id: str
    status: str
    progress: int = Field(ge=0)
    stage: str | None = None
    error_code: str | None = None
    created_at: float | None = None
    title: str | None = None
    platform: str | None = None
    url: str | None = None


class UnifiedJobListResponse(StrictModel):
    """R9 (review round 4): the default view's ONE-snapshot answer — counts,
    exclusion, ordering, and the page slice all come from a single SQLite
    read transaction, so no concurrent writer commit can make the same job
    appear in both segments."""

    items: list[UnifiedJobListItem]
    total: int = Field(ge=0)
    jobs_total: int = Field(ge=0)
    history_total: int = Field(ge=0)
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
    # F3 (feedback #9/#11/#3)
    title: str | None = None
    platform: str | None = None
    url: str | None = None
    subtitle_probe: Literal["found", "none", "unavailable"] | None = None
    blocked_reason: str | None = None
    blocked_message: str | None = None


class AppSettingsResponse(StrictModel):
    setup_completed: bool
    report_directory: str | None = None
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key_configured: bool = False
    # R1b: the endpoint the stored credential was saved for (frontend gating)
    llm_credential_origin: str | None = None
    asr_provider: str = "auto"
    whisper_cpp_executable: str | None = None


def _require_https_or_local_base_url(value: str | None) -> str | None:
    """Shared rule: HTTPS, except an explicit loopback endpoint."""
    if value is None:
        return None
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    if parsed.scheme == "https" and parsed.hostname:
        return value.rstrip("/")
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return value.rstrip("/")
    raise ValueError("base URL must use HTTPS, except for an explicit local endpoint")


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
        "sherpa-onnx-qwen3",
        "whisper-cpp-base",
    ] | None = None
    whisper_cpp_executable: str | None = None

    @field_validator("llm_base_url")
    @classmethod
    def validate_llm_base_url(cls, value: str | None) -> str | None:
        return _require_https_or_local_base_url(value)


class AppSettingsTestInput(StrictModel):
    """Payload for POST /api/settings/test (F1, CONFIGURATION.md).

    Carries the configuration the user is LOOKING at — the endpoint probes
    it and never persists anything.
    """

    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None

    @field_validator("llm_base_url")
    @classmethod
    def validate_llm_base_url(cls, value: str | None) -> str | None:
        return _require_https_or_local_base_url(value)


class SettingsTestResult(StrictModel):
    status: Literal[
        "ok",
        "auth_failed",
        "http_error",
        "network_error",
        "not_configured",
        "keyring_error",
    ]
    message: str


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


# --- P3 history / search / index API (docs/HISTORY_SEARCH_API.md) ---


class HistoryItem(StrictModel):
    job_id: str
    analysis_id: str
    title: str
    platform: str
    author: str
    video_id: str
    source_url: str
    published_at: float | None = None
    analyzed_at: float
    language: str
    summary_mode: str
    asr_provider: str
    asr_model: str
    asr_model_version: str
    tags: list[str]
    summary_preview: str
    file_path: str
    content_hash: str
    doc_status: str
    indexed_at: float


class HistoryListResponse(StrictModel):
    items: list[HistoryItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class HistoryDetailResponse(HistoryItem):
    core_summary: str | None = None


class ReportContentResponse(StrictModel):
    job_id: str
    section: str
    markdown: str
    file_path: str
    schema_version: int
    truncated: bool


class SearchHit(StrictModel):
    job_id: str
    title: str
    platform: str
    analyzed_at: float
    snippet: str
    matched_fields: list[str]
    doc_status: str


class SearchResponse(StrictModel):
    items: list[SearchHit]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    offset: int = Field(ge=0)


class RebuildCounters(StrictModel):
    scanned: int = 0
    indexed: int = 0
    unchanged: int = 0
    quarantined: int = 0
    duplicates: int = 0
    removed: int = 0
    purged: int = 0


class IndexStatusResponse(StrictModel):
    state: str
    last_finished_at: float | None = None
    last_result: RebuildCounters
    open_issues: int
    last_error_code: str | None = None


class IndexIssueItem(StrictModel):
    issue_code: str
    relative_path: str
    detail: str | None = None
    first_seen_at: float
    last_seen_at: float
    resolved_at: float | None = None


class IndexIssuesResponse(StrictModel):
    items: list[IndexIssueItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class IndexRebuildRequest(StrictModel):
    purge_missing: bool = False


class IndexRebuildAcceptedResponse(StrictModel):
    state: str


# --- P4 diagnostics (docs/MCP_TOOLS.md §7) ---


class DiagnosticCheckResponse(StrictModel):
    name: str
    status: Literal["pass", "warning", "fail", "skipped"]
    message: str
    detail: str | None = None


class DiagnosticsResponse(StrictModel):
    engine_version: str
    overall: Literal["pass", "warning", "fail"]
    checks: list[DiagnosticCheckResponse]
    redacted: Literal[True] = True


# --- P5 client config (docs/CLIENT_CONFIG_WRITE_CONTRACT.md §7/§8) ---


class CopyableConfigResponse(StrictModel):
    client_id: str
    format: Literal["toml", "json", "cli"]
    config_text: str
    target_path: str | None = None
    steps: tuple[str, ...] = ()


class ClientStatusResponse(StrictModel):
    client_id: str
    display_name: str
    tier: Literal["file_auto", "cli", "manual"]
    target_present: bool | None = None
    installed: bool | None = None
    entry_matches_current: bool | None = None
    config_supported: bool = True
    handshake: Literal["verified", "unverified", "failed"]
    handshake_reason: str | None = None
    handshake_checked_at: float | None = None
    engine_online: bool | None = None
    other_server_count: int | None = None
    backup_count: int = 0
    notes: tuple[str, ...] = ()


class ClientListResponse(StrictModel):
    clients: list[ClientStatusResponse]
    display_labels: dict[str, str]


class ClientOperationRequest(StrictModel):
    force: bool = False


class ClientRemoveRequest(StrictModel):
    confirm: bool = False


class ClientRestoreRequest(StrictModel):
    backup_name: str
    confirm: bool = False


class ClientOperationResponse(StrictModel):
    client_id: str
    operation: str
    performed: bool
    installed: bool | None = None
    entry_matches_current: bool | None = None
    handshake: Literal["verified", "unverified", "failed"]
    handshake_reason: str | None = None
    handshake_checked_at: float | None = None
    backup_name: str | None = None
    restored_from: str | None = None
    safety_backup: str | None = None
    removed_target: bool | None = None
    copyable: CopyableConfigResponse | None = None
    message: str = ""


class BackupInfoResponse(StrictModel):
    name: str
    created_at: float
    size_bytes: int
    sha256: str
    was_absent: bool = False
    matches_current: bool | None = None


class BackupListResponse(StrictModel):
    items: list[BackupInfoResponse]
