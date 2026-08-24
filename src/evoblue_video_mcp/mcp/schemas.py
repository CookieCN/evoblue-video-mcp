"""Transport-neutral Pydantic models for the seven MCP tools."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from evoblue_video_mcp.jobs import JobStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolError(StrictModel):
    code: str
    message: str
    retryable: bool = False
    detail: str | None = None


class SubmitVideoAnalysisInput(StrictModel):
    url: HttpUrl
    mode: Literal["auto", "standard", "unboxing"] = "auto"
    asr: Literal["auto", "disabled", "required"] = "auto"
    language: str | None = Field(default=None, max_length=64)


class SubmitVideoAnalysisOutput(StrictModel):
    job_id: str
    status: Literal[JobStatus.QUEUED] = JobStatus.QUEUED
    next_action: Literal["get_analysis_status"] = "get_analysis_status"
    reused: bool = False


class JobIdInput(StrictModel):
    job_id: str = Field(min_length=1, max_length=64)


class AnalysisStatusOutput(StrictModel):
    job_id: str
    status: JobStatus
    progress: int | None = Field(default=None, ge=0, le=100)
    stage: JobStatus | None = None
    message: str
    retryable: bool = False
    error_code: str | None = None
    error_detail: str | None = None


ReportSection = Literal[
    "summary", "outline", "marketing", "comments", "metadata", "transcript", "full"
]


class GetAnalysisReportInput(JobIdInput):
    section: ReportSection = "summary"


class AnalysisReportOutput(StrictModel):
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


class ListAnalysisJobsOutput(StrictModel):
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


class SearchAnalysisHistoryOutput(StrictModel):
    items: list[AnalysisSearchHit]
    limit: int = Field(ge=1, le=50)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class CancelAnalysisOutput(StrictModel):
    job_id: str
    status: JobStatus
    accepted: bool
    message: str


class DiagnoseEnvironmentInput(StrictModel):
    include_network: bool = False


class DiagnosticCheck(StrictModel):
    name: str
    status: Literal["pass", "warning", "fail", "skipped"]
    message: str
    detail: str | None = None


class DiagnoseEnvironmentOutput(StrictModel):
    overall: Literal["pass", "warning", "fail"]
    checks: list[DiagnosticCheck]
    redacted: Literal[True] = True

