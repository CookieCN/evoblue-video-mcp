"""Transport-neutral Pydantic models for the seven MCP tools.

Contract mirror of ``docs/MCP_TOOLS.md`` (P4 frozen). Success payloads carry a
flat ``ok: true`` field (:class:`SuccessEnvelope`); failures are
:class:`ToolFailure`. Each tool exposes a ``XxxResult`` discriminated-union
``RootModel`` alias: wrapping the union in a RootModel keeps the JSON schema
top-level (``oneOf``) and the serialized payload flat — a bare union return
annotation would make the SDK wrap it as ``{"result": ...}`` (ADR 0004).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, RootModel

from evoblue_video_mcp.jobs import JobStatus

TOOL_NAMES: tuple[str, ...] = (
    "submit_video_analysis",
    "get_analysis_status",
    "get_analysis_report",
    "list_analysis_jobs",
    "search_analysis_history",
    "cancel_analysis",
    "diagnose_environment",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolError(StrictModel):
    code: str
    message: str
    retryable: bool = False
    detail: str | None = None


class SuccessEnvelope(StrictModel):
    """Base for success payloads: flat business fields plus ``ok: true``."""

    ok: Literal[True] = True


class ToolFailure(StrictModel):
    """Failure payload: ``ok: false`` plus the frozen error envelope."""

    ok: Literal[False] = False
    error: ToolError


class SubmitVideoAnalysisInput(StrictModel):
    url: HttpUrl
    mode: Literal["auto", "standard", "unboxing"] = "auto"
    asr: Literal["auto", "disabled", "required"] = "auto"
    language: str | None = Field(default=None, max_length=64)


class SubmitVideoAnalysisOutput(SuccessEnvelope):
    job_id: str
    # "queued" for a fresh submission; a reused active job reports its current
    # state — the contract example shows the fresh case only.
    status: JobStatus
    next_action: Literal["get_analysis_status"] = "get_analysis_status"
    reused: bool = False


class JobIdInput(StrictModel):
    job_id: str = Field(min_length=1, max_length=64)


class AnalysisStatusOutput(SuccessEnvelope):
    job_id: str
    status: JobStatus
    progress: int | None = Field(default=None, ge=0, le=100)
    stage: str | None = Field(default=None, max_length=32)
    message: str
    retryable: bool = False
    error_code: str | None = None
    error_detail: str | None = None


ReportSection = Literal[
    "summary", "outline", "marketing", "comments", "metadata", "transcript", "full"
]


class GetAnalysisReportInput(JobIdInput):
    section: ReportSection = "summary"


class AnalysisReportOutput(SuccessEnvelope):
    job_id: str
    section: ReportSection
    markdown: str
    file_path: str
    schema_version: int = Field(ge=1)
    truncated: bool = False


class ListAnalysisJobsInput(StrictModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    status: JobStatus | None = None
    platform: str | None = Field(default=None, max_length=64)
    query: str | None = Field(default=None, max_length=200)


class AnalysisJobListItem(StrictModel):
    job_id: str
    title: str | None = None
    platform: str | None = None
    status: JobStatus
    progress: int | None = Field(default=None, ge=0, le=100)


class ListAnalysisJobsOutput(SuccessEnvelope):
    items: list[AnalysisJobListItem]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class SearchAnalysisHistoryInput(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class AnalysisSearchHit(StrictModel):
    job_id: str
    title: str
    matched_fields: list[str]
    snippet: str


class SearchAnalysisHistoryOutput(SuccessEnvelope):
    items: list[AnalysisSearchHit]
    limit: int = Field(ge=1, le=50)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class CancelAnalysisOutput(SuccessEnvelope):
    job_id: str
    status: JobStatus
    accepted: bool
    message: str


class DiagnoseEnvironmentInput(StrictModel):
    include_network: bool = False


CheckName = Literal[
    "local_engine",
    "engine_version",
    "database",
    "report_directory",
    "disk_space",
    "ffmpeg",
    "yt_dlp",
    "llm_config",
    "llm_api",
    "asr_runtime",
    "gpu",
    "asr_models",
    "cookie_browser",
    "worker_runtime",
]
"""Frozen diagnostics check names; order here matches MCP_TOOLS.md §7."""

CHECK_NAMES: tuple[str, ...] = (
    "local_engine",
    "engine_version",
    "database",
    "report_directory",
    "disk_space",
    "ffmpeg",
    "yt_dlp",
    "llm_config",
    "llm_api",
    "asr_runtime",
    "gpu",
    "asr_models",
    "cookie_browser",
    "worker_runtime",
)


class DiagnosticCheck(StrictModel):
    name: CheckName
    status: Literal["pass", "warning", "fail", "skipped"]
    message: str
    detail: str | None = None


class DiagnoseEnvironmentOutput(SuccessEnvelope):
    overall: Literal["pass", "warning", "fail"]
    checks: list[DiagnosticCheck]
    redacted: Literal[True] = True


SubmitVideoAnalysisResult = RootModel[
    Annotated[SubmitVideoAnalysisOutput | ToolFailure, Field(discriminator="ok")]
]
AnalysisStatusResult = RootModel[
    Annotated[AnalysisStatusOutput | ToolFailure, Field(discriminator="ok")]
]
AnalysisReportResult = RootModel[
    Annotated[AnalysisReportOutput | ToolFailure, Field(discriminator="ok")]
]
ListAnalysisJobsResult = RootModel[
    Annotated[ListAnalysisJobsOutput | ToolFailure, Field(discriminator="ok")]
]
SearchAnalysisHistoryResult = RootModel[
    Annotated[SearchAnalysisHistoryOutput | ToolFailure, Field(discriminator="ok")]
]
CancelAnalysisResult = RootModel[
    Annotated[CancelAnalysisOutput | ToolFailure, Field(discriminator="ok")]
]
DiagnoseEnvironmentResult = RootModel[
    Annotated[DiagnoseEnvironmentOutput | ToolFailure, Field(discriminator="ok")]
]
