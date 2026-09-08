"""Local Engine FastAPI factory."""

import asyncio
import logging
import platform
import secrets
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.params import Depends as DependsParam
from fastapi.responses import JSONResponse
from httpx import AsyncClient as _HttpClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typing_extensions import TypedDict

from evoblue_video_mcp import __version__
from evoblue_video_mcp.application.client_config.errors import ClientConfigError
from evoblue_video_mcp.application.client_config.models import HANDSHAKE_LABELS
from evoblue_video_mcp.application.client_config.service import (
    ClientConfigService,
    ClientStatus,
    OperationResult,
)
from evoblue_video_mcp.application.diagnostics import collect_diagnostics, redact_path
from evoblue_video_mcp.application.submit import compute_config_fingerprint, submit_video
from evoblue_video_mcp.asr.service import ModelManagerService, ModelSummary
from evoblue_video_mcp.config import CredentialStore, Settings, llm_credential_reference
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.platforms.detector import PlatformError
from evoblue_video_mcp.reports.parser import (
    MarkdownParseError,
    ParsedReport,
    frontmatter_block,
    parse_markdown_report,
)
from evoblue_video_mcp.storage.db import immediate_write_transaction
from evoblue_video_mcp.storage.fts import QueryInvalidError, build_match_query
from evoblue_video_mcp.storage.models import Job
from evoblue_video_mcp.storage.rebuild import (
    IndexRebuildService,
    RebuildRootUnavailable,
    read_report_pointer,
    resolve_report_root,
    write_report_pointer,
)
from evoblue_video_mcp.storage.report_repository import (
    ReportDocumentRecord,
    count_index_issues,
    count_open_issues,
    get_index_status,
    get_report_document,
    list_open_issues,
    list_report_documents,
    search_reports,
)
from evoblue_video_mcp.storage.repository import (
    get_app_settings,
    get_job,
    list_jobs,
    request_cancellation,
    save_app_settings,
)
from evoblue_video_mcp.web.schemas import (
    AppSettingsResponse,
    AppSettingsUpdate,
    BackupInfoResponse,
    BackupListResponse,
    ClientListResponse,
    ClientOperationRequest,
    ClientOperationResponse,
    ClientRemoveRequest,
    ClientRestoreRequest,
    ClientStatusResponse,
    CopyableConfigResponse,
    DiagnosticCheckResponse,
    DiagnosticsResponse,
    HistoryDetailResponse,
    HistoryItem,
    HistoryListResponse,
    IndexIssueItem,
    IndexIssuesResponse,
    IndexRebuildAcceptedResponse,
    IndexRebuildRequest,
    IndexStatusResponse,
    JobDetailResponse,
    JobListItem,
    JobListResponse,
    ModelListResponse,
    ModelSummaryResponse,
    RebuildCounters,
    ReportContentResponse,
    SearchHit,
    SearchResponse,
    SubmitJobInput,
    SubmitJobResponse,
    UninstallResponse,
)

#: Frozen response character cap for report sections (HISTORY_SEARCH_API §3).
_REPORT_SECTION_CAP = 50_000

logger = logging.getLogger(__name__)

#: P3 endpoints use the structured error envelope — including FastAPI's
#: pre-endpoint parameter validation, which would otherwise emit the legacy
#: ``{"detail": ...}`` shape (HISTORY_SEARCH_API §0). P4 diagnostics joins the
#: same envelope from birth (ADR 0004); P5 client-config joins likewise
#: (CLIENT_CONFIG_WRITE_CONTRACT §7).
_P3_ENVELOPE_PREFIXES = (
    "/api/history",
    "/api/search",
    "/api/index",
    "/api/diagnostics",
    "/api/mcp-clients",
)

_LLM_PROBE_TIMEOUT_S = 10.0


async def _credential_is_configured(
    credential_store: CredentialStore | None, reference: str | None
) -> bool:
    """Best-effort credential existence probe; failures mean "not configured"."""
    if credential_store is None or reference is None:
        return False
    try:
        return bool(await asyncio.to_thread(credential_store.get_secret, reference))
    except Exception:
        return False


def _llm_status_probe() -> Callable[[str], Awaitable[int]]:
    """Build an injectable GET-status probe for the diagnostics network check.

    Takes the full URL (collect_diagnostics applies the configured base_url)
    so tests can inject a fake and never touch the network.
    """

    async def probe(url: str) -> int:
        async with _HttpClient(timeout=_LLM_PROBE_TIMEOUT_S) as client:
            response = await client.get(url)
            return response.status_code

    return probe


class HealthResponse(TypedDict):
    status: Literal["ok"]
    service: str
    version: str


class HistoryApiError(Exception):
    """P3 endpoint failure carrying the frozen ``{"error": {...}}`` envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _local_token_dependency(token: str) -> Callable[[str | None], Awaitable[None]]:
    async def require_local_token(x_local_token: str | None = Header(default=None)) -> None:
        if not token:
            return
        if x_local_token is None or not secrets.compare_digest(x_local_token, token):
            raise HTTPException(status_code=401, detail="invalid local access token")

    return require_local_token


def _to_list_item(job: Job) -> JobListItem:
    return JobListItem(
        job_id=job.job_id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        error_code=job.error_code,
        created_at=job.created_at,
    )


def _to_detail(job: Job) -> JobDetailResponse:
    return JobDetailResponse(
        job_id=job.job_id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        error_code=job.error_code,
        error_detail=job.error_detail,
        retryable=job.retryable,
        created_at=job.created_at,
        updated_at=job.updated_at,
        asr_provider_id=job.asr_provider_id,
        asr_model_id=job.asr_model_id,
        asr_model_version=job.asr_model_version,
        asr_recommendation_model_id=job.asr_recommendation_model_id,
    )


def create_app(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    local_token: str | None = None,
    credential_store: CredentialStore | None = None,
    model_service: ModelManagerService | None = None,
    index_rebuild_service: IndexRebuildService | None = None,
    report_pointer_file: Path | None = None,
    fallback_report_root: Path | None = None,
    data_directory: Path | None = None,
    client_config_service: ClientConfigService | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """Create the Local Engine HTTP application.

    Data endpoints attach when a session factory is given; model-management
    endpoints attach when a ``model_service`` is also given; index-rebuild
    endpoints attach when an ``index_rebuild_service`` is also given;
    client-config endpoints attach when a ``client_config_service`` is given.
    When ``local_token`` (or ``Settings.local_access_token``) is set, data
    endpoints require it via the ``X-Local-Token`` header; ``/api/health``
    stays token-exempt.
    """
    app_settings = settings or Settings()
    token = local_token if local_token is not None else app_settings.local_access_token
    if app_settings.environment == "production" and not token:
        raise RuntimeError("local access token is required in production")
    app = FastAPI(title=app_settings.app_name, version=__version__, lifespan=lifespan)

    @app.exception_handler(HistoryApiError)
    async def _history_api_error_handler(
        request: Request, exc: HistoryApiError
    ) -> JSONResponse:
        # Frozen P3 error envelope (docs/HISTORY_SEARCH_API.md §0).
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(ClientConfigError)
    async def _client_config_error_handler(
        request: Request, exc: ClientConfigError
    ) -> JSONResponse:
        # P5 stable codes (CLIENT_CONFIG_WRITE_CONTRACT §7) — same envelope.
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Parameter type errors (e.g. limit=abc, open=maybe) are rejected by
        # FastAPI BEFORE the endpoint runs — P3 routes must still answer in the
        # frozen envelope; legacy endpoints keep FastAPI's default shape.
        if request.url.path.startswith("/api/mcp-clients"):
            # P5's frozen code table has no INVALID_FILTER — request-body
            # validation failures map to their own code (contract §7).
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "INVALID_REQUEST",
                        "message": "invalid request body",
                    }
                },
            )
        if request.url.path.startswith(_P3_ENVELOPE_PREFIXES):
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "INVALID_FILTER",
                        "message": "invalid query parameters",
                    }
                },
            )
        return JSONResponse(
            status_code=422, content={"detail": jsonable_encoder(exc.errors())}
        )

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return {"status": "ok", "service": app_settings.app_name, "version": __version__}

    if session_factory is not None:
        _register_data_endpoints(
            app, session_factory, token, credential_store, model_service,
            report_pointer_file,
        )
        _register_history_endpoints(
            app, session_factory, token, index_rebuild_service
        )
        _register_diagnostics_endpoint(
            app,
            session_factory,
            token,
            credential_store=credential_store,
            model_service=model_service,
            fallback_report_root=fallback_report_root,
            pointer_file=report_pointer_file,
            data_directory=data_directory,
        )

    if client_config_service is not None:
        _register_client_config_endpoints(app, token, client_config_service)

    return app


def _register_diagnostics_endpoint(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    token: str,
    *,
    credential_store: CredentialStore | None,
    model_service: ModelManagerService | None,
    fallback_report_root: Path | None,
    pointer_file: Path | None,
    data_directory: Path | None,
) -> None:
    """GET /api/diagnostics — Engine-side checks for the MCP diagnose tool.

    Contract docs/MCP_TOOLS.md §7: a single failing check degrades only itself,
    output is redacted, and the network probe runs only on explicit request.
    Errors answer in the frozen structured envelope (registered in
    ``_P3_ENVELOPE_PREFIXES``).
    """
    dependencies = [Depends(_local_token_dependency(token))] if token else []

    @app.get(
        "/api/diagnostics",
        response_model=DiagnosticsResponse,
        dependencies=dependencies,
    )
    async def diagnostics(include_network: bool = False) -> DiagnosticsResponse:
        report = await collect_diagnostics(
            session_factory,
            credential_store=credential_store,
            model_service=model_service,
            fallback_report_root=fallback_report_root,
            pointer_file=pointer_file,
            data_directory=data_directory,
            include_network=include_network,
            http_get=_llm_status_probe(),
        )
        return DiagnosticsResponse(
            engine_version=report.engine_version,
            overall=report.overall,
            checks=[
                DiagnosticCheckResponse(
                    name=check.name,
                    status=check.status,
                    message=check.message,
                    detail=check.detail,
                )
                for check in report.checks
            ],
            redacted=True,
        )

    @app.get("/api/diagnostics/export", dependencies=dependencies)
    async def diagnostics_export() -> JSONResponse:
        """Redacted diagnostics bundle for bug reports (P8-003).

        Contract: errors answer in the frozen JSON envelope (the path sits
        under the /api/diagnostics prefix); success is a JSON attachment. The
        payload reuses the redacted diagnostics report and adds only
        non-secret context - paths appear as redacted tails, the whisper CLI
        surfaces as a boolean, and no credential reference is included.
        """
        import sys as _sys
        from datetime import date, datetime

        report = await collect_diagnostics(
            session_factory,
            credential_store=credential_store,
            model_service=model_service,
            fallback_report_root=fallback_report_root,
            pointer_file=pointer_file,
            data_directory=data_directory,
            include_network=False,
            http_get=_llm_status_probe(),
        )
        async with session_factory() as sess:
            row = await get_app_settings(sess)
            open_issues = await count_open_issues(sess)
        base_host = None
        if row is not None and row.llm_base_url:
            base_host = urllib.parse.urlsplit(row.llm_base_url).hostname
        payload = {
            "kind": "evoblue-diagnostics",
            "exported_at": datetime.now(UTC).isoformat(),
            "app_version": __version__,
            "platform": _sys.platform,
            "python_version": platform.python_version(),
            "overall": report.overall,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "message": check.message,
                    "detail": check.detail,
                }
                for check in report.checks
            ],
            "settings": {
                "setup_completed": bool(row.setup_completed) if row else False,
                "llm_provider": row.llm_provider if row else None,
                "llm_model": row.llm_model if row else None,
                "llm_base_url_host": base_host,
                "asr_provider": row.asr_provider if row else "auto",
                "report_directory": (
                    redact_path(Path(row.report_directory))
                    if row is not None and row.report_directory
                    else None
                ),
                "whisper_cli_configured": (
                    bool(row.whisper_cpp_executable) if row else False
                ),
            },
            "index": {"open_issues": open_issues},
            "redacted": True,
        }
        filename = f"evoblue-diagnostics-{__version__}-{date.today().isoformat()}.json"
        return JSONResponse(
            content=payload,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


def _register_data_endpoints(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    token: str,
    credential_store: CredentialStore | None,
    model_service: ModelManagerService | None,
    report_pointer_file: Path | None = None,
) -> None:
    dependencies = [Depends(_local_token_dependency(token))] if token else []

    # §4 dual-write discipline for report_directory: "read current → write
    # recovery pointer → commit database" is SERIALIZED in-process (two
    # concurrent PUTs must not interleave into pointer=B / database=A) and
    # COMPENSATED (a database failure restores the previous pointer, so a 503
    # never leaves the pointer ahead of the database). Two stores cannot be
    # made atomic; this bounds the divergence to a compensated-then-failed
    # double fault, which logs loudly.
    settings_write_lock = asyncio.Lock()

    @app.get("/api/jobs", response_model=JobListResponse, dependencies=dependencies)
    async def jobs_list(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        status: JobStatus | None = None,
    ) -> JobListResponse:
        async with session_factory() as sess:
            items, total = await list_jobs(sess, limit=limit, offset=offset, status=status)
        return JobListResponse(
            items=[_to_list_item(job) for job in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/jobs/{job_id}", response_model=JobDetailResponse, dependencies=dependencies)
    async def job_detail(job_id: str) -> JobDetailResponse:
        async with session_factory() as sess:
            job = await get_job(sess, job_id=job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _to_detail(job)

    @app.post(
        "/api/jobs/{job_id}/cancel",
        response_model=JobDetailResponse,
        dependencies=dependencies,
    )
    async def job_cancel(job_id: str) -> JobDetailResponse:
        # §6: the cancel reads the job then writes — as an IMMEDIATE
        # transaction, a concurrent writer committing in between degrades
        # to the busy timeout instead of an unretryable BUSY_SNAPSHOT (P4
        # brings concurrent MCP clients cancelling while the worker
        # writes).
        async with session_factory() as sess, immediate_write_transaction(sess):
            job = await request_cancellation(sess, job_id=job_id, now=time.time())
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _to_detail(job)

    @app.get("/api/settings", response_model=AppSettingsResponse, dependencies=dependencies)
    async def settings_get() -> AppSettingsResponse:
        async with session_factory() as sess:
            current = await get_app_settings(sess)
        if current is None:
            return AppSettingsResponse(setup_completed=False)
        return AppSettingsResponse(
            setup_completed=current.setup_completed,
            report_directory=current.report_directory,
            llm_provider=current.llm_provider,
            llm_base_url=current.llm_base_url,
            llm_model=current.llm_model,
            llm_api_key_configured=await _credential_is_configured(
                credential_store, current.llm_credential_ref
            ),
            asr_provider=current.asr_provider or "auto",
            whisper_cpp_executable=current.whisper_cpp_executable,
        )

    @app.put("/api/settings", response_model=AppSettingsResponse, dependencies=dependencies)
    async def settings_put(payload: AppSettingsUpdate) -> AppSettingsResponse:
        fields = payload.model_fields_set
        async with settings_write_lock:
            async with session_factory() as sess:
                current = await get_app_settings(sess)
            provider = (
                payload.llm_provider
                if "llm_provider" in fields
                else (current.llm_provider if current else None)
            )
            credential_ref = current.llm_credential_ref if current else None
            if "llm_provider" in fields and provider != (current.llm_provider if current else None):
                credential_ref = llm_credential_reference(provider or "")
            if "llm_api_key" in fields and payload.llm_api_key:
                credential_ref = llm_credential_reference(provider or "")

            # Runnable-setup invariant (P1 review rounds 2-3): the FINAL state
            # after this save must pass the same gate
            # ProductionHandlerFactory applies before a worker claims jobs —
            # provider, base URL, model, credential ref all present AND the
            # keyring actually holds the key for the final ref (the payload's
            # own key counts). The gate runs whenever the final state is
            # completed, NOT only when the payload explicitly carries
            # setup_completed=true: otherwise a partial update (delete key,
            # switch provider, clear model) on an already-completed record
            # would silently preserve the true flag and re-create the
            # "configured but worker idle" deadlock. An explicit
            # setup_completed=false downgrades and skips the gate by design.
            # Checked here, before ANY side effect (keyring write, pointer
            # write, database commit): the frontend can be bypassed or simply
            # wrong, so this is the authority.
            final_setup_completed = (
                payload.setup_completed
                if payload.setup_completed is not None
                else (current.setup_completed if current else False)
            )
            if final_setup_completed:
                merged_base_url = (
                    payload.llm_base_url
                    if "llm_base_url" in fields
                    else (current.llm_base_url if current else None)
                )
                merged_model = (
                    payload.llm_model
                    if "llm_model" in fields
                    else (current.llm_model if current else None)
                )
                key_in_payload = "llm_api_key" in fields and bool(
                    payload.llm_api_key and payload.llm_api_key.get_secret_value()
                )
                # an explicit empty llm_api_key DELETES the stored secret later
                # in this handler — the stored key must not count as configured
                key_deleted = "llm_api_key" in fields and not key_in_payload
                key_stored = False
                if (
                    not key_in_payload
                    and not key_deleted
                    and credential_store is not None
                    and credential_ref
                ):
                    try:
                        key_stored = bool(
                            await asyncio.to_thread(
                                credential_store.get_secret, credential_ref
                            )
                        )
                    except Exception:
                        key_stored = False
                if not (
                    provider
                    and merged_base_url
                    and merged_model
                    and credential_ref
                    and (key_in_payload or key_stored)
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "无法完成设置：LLM 配置不可运行"
                            "（Provider / Base URL / 模型 / API Key 必须齐全，"
                            "API Key 需已存入凭据库；切换 Provider 后必须填写新 Key）"
                        ),
                    )

            if "llm_api_key" in fields:
                if credential_store is None:
                    raise HTTPException(status_code=503, detail="credential store unavailable")
                credential_ref = llm_credential_reference(provider or "")
                secret = payload.llm_api_key.get_secret_value() if payload.llm_api_key else ""
                try:
                    if secret:
                        await asyncio.to_thread(
                            credential_store.set_secret, credential_ref, secret
                        )
                    else:
                        await asyncio.to_thread(credential_store.delete_secret, credential_ref)
                        credential_ref = None
                except Exception as exc:
                    raise HTTPException(
                        status_code=503, detail="credential store unavailable"
                    ) from exc
            report_directory_value = (
                payload.report_directory
                if "report_directory" in fields
                else (current.report_directory if current else None)
            )
            pointer_dir = (
                report_pointer_file.parent
                if report_pointer_file is not None and "report_directory" in fields
                else None
            )
            pointer_value = (
                Path(report_directory_value) if report_directory_value else None
            )
            previous_pointer: Path | None = None

            def _restore_pointer() -> None:
                """Put the pointer back to its pre-request value — inline (no
                thread): with the inline write above this is an indivisible
                critical section on this event loop, so a cancellation can
                never land between the replace and the restore, and a
                cancelled ``to_thread`` can never surface a late replace that
                re-overwrites the restored pointer."""
                if pointer_dir is None:
                    return
                try:
                    write_report_pointer(pointer_dir, previous_pointer)
                except OSError:
                    logger.error(
                        "report pointer compensation failed after a failed "
                        "settings save: OSError "
                        "(code=REPORT_POINTER_COMPENSATION_FAILED)"
                    )

            if pointer_dir is not None:
                # §4 dual-write policy: the recovery pointer goes FIRST
                # (atomically) — by the time the API answers success, pointer
                # and database agree. A pointer failure aborts the whole save
                # (503) with the database untouched; the reverse ordering
                # would let the database adopt a directory the recovery
                # pointer does not know — a split-brain that only surfaces
                # after SQLite deletion. Clearing writes an empty tombstone
                # through the same atomic path, and ANY pointer failure
                # (including a failed clear) refuses the database commit — a
                # silently cleared pointer would let a stale directory revive
                # after the database is deleted.
                try:
                    previous_pointer = read_report_pointer(pointer_dir)
                except RebuildRootUnavailable as exc:
                    raise HTTPException(
                        status_code=503, detail="report pointer could not be read"
                    ) from exc
                # Inline write (not ``to_thread``): see _restore_pointer — a
                # cancelled thread would keep running and could re-overwrite
                # the compensated pointer AFTER the restore. Settings saves
                # are low-frequency; the brief fsync on the event loop is the
                # price of the indivisible critical section.
                try:
                    write_report_pointer(pointer_dir, pointer_value)
                except OSError as exc:
                    raise HTTPException(
                        status_code=503, detail="report pointer could not be updated"
                    ) from exc
            try:
                # §6: reads-then-writes run as BEGIN IMMEDIATE so a
                # concurrent writer committing in between cannot kill the
                # save with an unretryable BUSY_SNAPSHOT.
                #
                # §4 exact compensation: ``commit=False`` keeps the commit in
                # THIS transaction, as the LAST statement before the context
                # exits — there is no post-commit await left inside the try.
                # Therefore reaching the handlers below PROVES the database
                # did not adopt the value, and restoring the pointer is
                # always correct; a failure AFTER the commit (reconcile,
                # response encoding) never enters these handlers, so the
                # stores can never be pushed apart by compensation itself.
                async with session_factory() as sess, immediate_write_transaction(sess):
                    saved = await save_app_settings(
                        sess,
                        setup_completed=(
                            payload.setup_completed
                            if payload.setup_completed is not None
                            else (current.setup_completed if current else False)
                        ),
                        now=time.time(),
                        report_directory=report_directory_value,
                        llm_provider=provider,
                        llm_base_url=(
                            payload.llm_base_url
                            if "llm_base_url" in fields
                            else (current.llm_base_url if current else None)
                        ),
                        llm_model=(
                            payload.llm_model
                            if "llm_model" in fields
                            else (current.llm_model if current else None)
                        ),
                        llm_credential_ref=credential_ref,
                        asr_provider=(
                            payload.asr_provider
                            if "asr_provider" in fields
                            else ((current.asr_provider if current else None) or "auto")
                        ),
                        whisper_cpp_executable=(
                            payload.whisper_cpp_executable
                            if "whisper_cpp_executable" in fields
                            else (current.whisper_cpp_executable if current else None)
                        ),
                        commit=False,
                    )
            except asyncio.CancelledError:
                # A PUT cancelled between the pointer replace and the database
                # commit must not leave the pointer ahead of the database (§4)
                # — compensation covers CANCELLATION, then the cancel
                # propagates.
                _restore_pointer()
                raise
            except Exception as exc:
                # The database did NOT adopt the value: put the pointer back
                # so the stores cannot diverge behind a 503 (§4).
                _restore_pointer()
                raise HTTPException(
                    status_code=503, detail="settings could not be saved"
                ) from exc
        if "whisper_cpp_executable" in fields and model_service is not None:
            # A newly usable CLI can unblock jobs parked on a whisper
            # recommendation; provider registration and the resume both happen
            # inside the reconciliation, retrying providers whose last load
            # failed (the settings event may have fixed them).
            await model_service.reconcile_waiting(retry_failed_loads=True)
        return AppSettingsResponse(
            setup_completed=saved.setup_completed,
            report_directory=saved.report_directory,
            llm_provider=saved.llm_provider,
            llm_base_url=saved.llm_base_url,
            llm_model=saved.llm_model,
            llm_api_key_configured=await _credential_is_configured(
                credential_store, saved.llm_credential_ref
            ),
            asr_provider=saved.asr_provider or "auto",
            whisper_cpp_executable=saved.whisper_cpp_executable,
        )

    @app.post("/api/jobs", response_model=SubmitJobResponse, dependencies=dependencies)
    async def submit_job(payload: SubmitJobInput) -> SubmitJobResponse:
        try:
            async with session_factory() as sess:
                app_settings = await get_app_settings(sess)
                provider = (app_settings.llm_provider if app_settings else None) or ""
                base_url = (app_settings.llm_base_url if app_settings else None) or ""
                model = (app_settings.llm_model if app_settings else None) or ""
                config_fp = compute_config_fingerprint(
                    provider=provider,
                    base_url=base_url,
                    model=model,
                    asr_provider=(app_settings.asr_provider if app_settings else None) or "auto",
                )
                job, reused = await submit_video(
                    sess,
                    url=payload.url,
                    mode=payload.mode,
                    asr=payload.asr,
                    language=payload.language,
                    config_fingerprint=config_fp,
                    now=time.time(),
                    reuse_window_seconds=3600.0,
                )
        except PlatformError as exc:
            raise HTTPException(status_code=422, detail=exc.error_code) from exc
        return SubmitJobResponse(job_id=job.job_id, status=job.status, reused=reused)

    if model_service is not None:
        _register_model_endpoints(app, model_service, dependencies)


def _to_model_summary(summary: ModelSummary) -> ModelSummaryResponse:
    return ModelSummaryResponse(
        model_id=summary.model_id,
        version=summary.version,
        tier=summary.tier,
        provider=summary.provider,
        languages=list(summary.languages),
        compressed_size_bytes=summary.compressed_size_bytes,
        installed_size_bytes=summary.installed_size_bytes,
        license=summary.license,
        attribution=summary.attribution,
        redistribution=summary.redistribution,
        installed=summary.installed,
        active=summary.active,
        installed_path=summary.installed_path,
        status=summary.status,
        downloaded_bytes=summary.downloaded_bytes,
        error_code=summary.error_code,
        formal_default=summary.formal_default,
    )


def _register_model_endpoints(
    app: FastAPI,
    model_service: ModelManagerService,
    dependencies: list[DependsParam],
) -> None:
    @app.get("/api/models", response_model=ModelListResponse, dependencies=dependencies)
    async def models_list() -> ModelListResponse:
        items = await model_service.list_models()
        return ModelListResponse(items=[_to_model_summary(s) for s in items])

    @app.post(
        "/api/models/{model_id}/install",
        response_model=ModelSummaryResponse,
        dependencies=dependencies,
        status_code=202,
    )
    async def model_install(model_id: str) -> ModelSummaryResponse:
        try:
            summary = await model_service.install(model_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="model not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _to_model_summary(summary)

    @app.post(
        "/api/models/{model_id}/cancel",
        response_model=ModelSummaryResponse,
        dependencies=dependencies,
    )
    async def model_cancel(model_id: str) -> ModelSummaryResponse:
        try:
            summary = await model_service.cancel(model_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="model not found") from exc
        return _to_model_summary(summary)

    @app.delete(
        "/api/models/{model_id}",
        response_model=UninstallResponse,
        dependencies=dependencies,
    )
    async def model_uninstall(model_id: str) -> UninstallResponse:
        try:
            result = await model_service.uninstall(model_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="model not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return UninstallResponse(
            model_id=model_id,
            reclaimed_bytes=result.reclaimed_bytes,
            pending_reclaim_bytes=result.pending_reclaim_bytes,
        )
def _to_history_item(record: ReportDocumentRecord) -> HistoryItem:
    return HistoryItem(
        job_id=record.job_id,
        analysis_id=record.analysis_id,
        title=record.title,
        platform=record.platform,
        author=record.author,
        video_id=record.video_id,
        source_url=record.source_url,
        published_at=record.published_at,
        analyzed_at=record.analyzed_at,
        language=record.language,
        summary_mode=record.summary_mode,
        asr_provider=record.asr_provider,
        asr_model=record.asr_model,
        asr_model_version=record.asr_model_version,
        tags=list(record.tags),
        summary_preview=record.summary_preview,
        file_path=record.relative_path,
        content_hash=record.content_hash,
        doc_status=record.doc_status,
        indexed_at=record.indexed_at,
    )


async def _read_report_text(
    session_factory: async_sessionmaker[AsyncSession],
    index_rebuild_service: IndexRebuildService | None,
    record: ReportDocumentRecord,
) -> tuple[str | None, str | None]:
    """Read the report file's CURRENT content (HISTORY_SEARCH_API §3); no hash
    enforcement — drift is the rebuild's concern. Returns ``(text, abs_path)``;
    ``(None, None)`` when the file is gone.

    The root uses the SAME §4 resolver as the rebuild engine (settings →
    pointer file → fallback): after a deleted-database recovery the settings
    row is gone, and the recovered index must be fully READABLE — history
    detail and section content — not merely listed and searchable.
    """
    try:
        if index_rebuild_service is not None:
            root = await index_rebuild_service.resolve_report_root()
        else:
            root = await resolve_report_root(session_factory)
    except RebuildRootUnavailable as exc:
        # An unreadable recovery pointer is fail-closed for the READ path too:
        # 503 "directory unavailable" — never a fallback read of the wrong
        # directory (§4).
        raise HistoryApiError(
            503, "SEARCH_INDEX_UNAVAILABLE", str(exc)
        ) from None
    if root is None:
        raise HistoryApiError(
            500, "REPORT_READ_FAILED", "report directory is not configured"
        )
    root_path = Path(root).resolve()
    path = (root_path / record.relative_path).resolve()
    if not path.is_relative_to(root_path):
        raise HistoryApiError(
            500, "REPORT_READ_FAILED", "report path escapes the report directory"
        )
    if not path.is_file():
        return None, None
    try:
        text = await asyncio.to_thread(path.read_text, "utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HistoryApiError(
            500, "REPORT_READ_FAILED", "failed to read the report file"
        ) from exc
    return text, str(path)


def _register_history_endpoints(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    token: str,
    index_rebuild_service: IndexRebuildService | None = None,
) -> None:
    dependencies = [Depends(_local_token_dependency(token))] if token else []

    @app.get("/api/history", response_model=HistoryListResponse, dependencies=dependencies)
    async def history_list(
        limit: int = 20,
        offset: int = 0,
        platform: str | None = None,
        language: str | None = None,
        asr_provider: str | None = None,
    ) -> HistoryListResponse:
        if not 1 <= limit <= 100 or offset < 0:
            raise HistoryApiError(422, "INVALID_FILTER", "limit/offset out of range")
        async with session_factory() as sess:
            records, total = await list_report_documents(
                sess,
                platform=platform,
                language=language,
                asr_provider=asr_provider,
                limit=limit,
                offset=offset,
            )
        return HistoryListResponse(
            items=[_to_history_item(r) for r in records],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/api/history/{job_id}",
        response_model=HistoryDetailResponse,
        dependencies=dependencies,
    )
    async def history_detail(job_id: str) -> HistoryDetailResponse:
        async with session_factory() as sess:
            record = await get_report_document(sess, job_id=job_id)
            if record is None:
                raise HistoryApiError(404, "HISTORY_ITEM_NOT_FOUND", "history item not found")
        core_summary: str | None = None
        if record.doc_status != "missing":
            text, _ = await _read_report_text(session_factory, index_rebuild_service, record)
            if text is not None:
                try:
                    core_summary = parse_markdown_report(text).core_summary
                except MarkdownParseError:
                    core_summary = None  # file unreadable as v1; metadata still returned
        item = _to_history_item(record)
        return HistoryDetailResponse(**item.model_dump(), core_summary=core_summary)

    @app.get(
        "/api/history/{job_id}/report",
        response_model=ReportContentResponse,
        dependencies=dependencies,
    )
    async def history_report(job_id: str, section: str = "summary") -> ReportContentResponse:
        valid_sections = (
            "summary",
            "outline",
            "marketing",
            "comments",
            "metadata",
            "transcript",
            "full",
        )
        if section not in valid_sections:
            raise HistoryApiError(422, "INVALID_FILTER", f"unknown section: {section}")
        async with session_factory() as sess:
            record = await get_report_document(sess, job_id=job_id)
            if record is None:
                raise HistoryApiError(404, "HISTORY_ITEM_NOT_FOUND", "history item not found")
        text, abs_path = await _read_report_text(session_factory, index_rebuild_service, record)
        if text is None or abs_path is None:
            raise HistoryApiError(410, "REPORT_FILE_MISSING", "report file is missing")
        try:
            parsed = parse_markdown_report(text)
        except MarkdownParseError:
            parsed = None
        if section == "full":
            content, schema_version = text, (parsed.schema_version if parsed else 1)
        else:
            if parsed is None:
                raise HistoryApiError(
                    404,
                    "SECTION_NOT_AVAILABLE",
                    "report file does not parse as Schema v1",
                )
            content = _extract_section(parsed, text, section)
            if not content.strip():
                raise HistoryApiError(404, "SECTION_NOT_AVAILABLE", "section has no content")
            # §3 responses are complete Markdown sections — heading included —
            # so MCP passthrough (P4 get_analysis_report) stays faithful.
            content = _SECTION_HEADINGS[section] + content
            schema_version = parsed.schema_version
        truncated = len(content) > _REPORT_SECTION_CAP
        return ReportContentResponse(
            job_id=job_id,
            section=section,
            markdown=content[:_REPORT_SECTION_CAP],
            file_path=abs_path,
            schema_version=schema_version,
            truncated=truncated,
        )

    @app.get("/api/search", response_model=SearchResponse, dependencies=dependencies)
    async def search(q: str = "", limit: int = 10, offset: int = 0) -> SearchResponse:
        if not 1 <= len(q) <= 500:
            raise HistoryApiError(422, "QUERY_INVALID", "query must be 1-500 characters")
        if not 1 <= limit <= 50 or offset < 0:
            raise HistoryApiError(422, "INVALID_FILTER", "limit/offset out of range")
        try:
            match_query = build_match_query(q)
        except QueryInvalidError as exc:
            raise HistoryApiError(422, "QUERY_INVALID", str(exc)) from None
        or_query = match_query.replace(" AND ", " OR ")
        async with session_factory() as sess:
            try:
                hits, total = await search_reports(
                    sess,
                    match_query=match_query,
                    or_query=or_query,
                    limit=limit,
                    offset=offset,
                )
            except SQLAlchemyError as exc:
                # Contract §0: a missing/corrupt FTS index is 503, not a bare
                # 500 — clients must tell "no results" from "index broken".
                raise HistoryApiError(
                    503, "SEARCH_INDEX_UNAVAILABLE", "search index is unavailable"
                ) from exc
        return SearchResponse(
            items=[
                SearchHit(
                    job_id=hit.job_id,
                    title=hit.title,
                    platform=hit.platform,
                    analyzed_at=hit.analyzed_at,
                    snippet=hit.snippet,
                    matched_fields=list(hit.matched_fields),
                    doc_status=hit.doc_status,
                )
                for hit in hits
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/api/index/status", response_model=IndexStatusResponse, dependencies=dependencies
    )
    async def index_status() -> IndexStatusResponse:
        async with session_factory() as sess:
            snapshot = await get_index_status(sess)
            open_issues = await count_open_issues(sess)
        return IndexStatusResponse(
            state=snapshot.state,
            last_finished_at=snapshot.finished_at,
            last_result=RebuildCounters(
                scanned=snapshot.scanned,
                indexed=snapshot.indexed,
                unchanged=snapshot.unchanged,
                quarantined=snapshot.quarantined,
                duplicates=snapshot.duplicates,
                removed=snapshot.removed,
                purged=snapshot.purged,
            ),
            open_issues=open_issues,
            last_error_code=snapshot.last_error_code,
        )

    @app.get(
        "/api/index/issues", response_model=IndexIssuesResponse, dependencies=dependencies
    )
    async def index_issues(
        open: bool = True, issue_code: str | None = None, limit: int = 50, offset: int = 0
    ) -> IndexIssuesResponse:
        if not 1 <= limit <= 100 or offset < 0:
            raise HistoryApiError(422, "INVALID_FILTER", "limit/offset out of range")
        async with session_factory() as sess:
            items = await list_open_issues(
                sess,
                issue_code=issue_code,
                open_only=open,
                limit=limit,
                offset=offset,
            )
            total = await count_index_issues(sess, issue_code=issue_code, open_only=open)
        return IndexIssuesResponse(
            items=[
                IndexIssueItem(
                    issue_code=issue.issue_code,
                    relative_path=issue.relative_path,
                    detail=issue.detail,
                    first_seen_at=issue.first_seen_at,
                    last_seen_at=issue.last_seen_at,
                    resolved_at=issue.resolved_at,
                )
                for issue in items
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.post(
        "/api/index/rebuild",
        response_model=IndexRebuildAcceptedResponse,
        status_code=202,
        dependencies=dependencies,
    )
    async def index_rebuild(
        payload: IndexRebuildRequest | None = None,
    ) -> IndexRebuildAcceptedResponse:
        if index_rebuild_service is None:
            raise HistoryApiError(
                503, "SEARCH_INDEX_UNAVAILABLE", "index rebuild service is unavailable"
            )
        purge = bool(payload.purge_missing) if payload is not None else False
        try:
            # pre-flight happens HERE (awaited): a missing report directory
            # surfaces as 503 before any 202 is returned (§4 fail-safe).
            started = await index_rebuild_service.start_rebuild(purge=purge)
        except RebuildRootUnavailable as exc:
            raise HistoryApiError(503, "SEARCH_INDEX_UNAVAILABLE", str(exc)) from None
        if not started:
            raise HistoryApiError(
                409, "REBUILD_ALREADY_RUNNING", "a rebuild is already running"
            )
        return IndexRebuildAcceptedResponse(state="running")


#: Frozen section headings — responses include them so the markdown payload is
#: a complete Markdown section (HISTORY_SEARCH_API §3 example).
_SECTION_HEADINGS: dict[str, str] = {
    "summary": "## 核心摘要\n\n",
    "outline": "## 时间轴大纲\n\n",
    "marketing": "## 内容分析\n\n",
    "comments": "## 评论风向\n\n",
    "metadata": "",  # composed from the frontmatter block + 视频信息
    "transcript": "## 完整字幕\n\n",
    "full": "",
}


def _extract_section(parsed: ParsedReport, text: str, section: str) -> str:
    if section == "summary":
        return parsed.core_summary
    if section == "outline":
        return parsed.timeline_outline
    if section == "marketing":
        return parsed.content_analysis
    if section == "comments":
        return parsed.comment_sentiment
    if section == "metadata":
        block = frontmatter_block(text).rstrip("\n")
        video_info = parsed.video_information
        body = f"## 视频信息\n\n{video_info}" if video_info else ""
        return f"{block}\n\n{body}" if body else block
    if section == "transcript":
        return parsed.transcript or ""
    raise HistoryApiError(422, "INVALID_FILTER", f"unknown section: {section}")


def _to_client_status(status: ClientStatus) -> ClientStatusResponse:
    return ClientStatusResponse(
        client_id=status.client_id,
        display_name=status.display_name,
        tier=status.tier,
        target_present=status.target_present,
        installed=status.installed,
        entry_matches_current=status.entry_matches_current,
        config_supported=status.config_supported,
        handshake=status.handshake,
        handshake_reason=status.handshake_reason,
        handshake_checked_at=status.handshake_checked_at,
        engine_online=status.engine_online,
        other_server_count=status.other_server_count,
        backup_count=status.backup_count,
        notes=status.notes,
    )


def _register_client_config_endpoints(
    app: FastAPI,
    token: str,
    service: ClientConfigService,
) -> None:
    """P5 client-config surface (docs/CLIENT_CONFIG_WRITE_CONTRACT.md §7).

    Registered only when a ``client_config_service`` is injected; errors
    answer in the structured envelope (``_P3_ENVELOPE_PREFIXES``). Field-level
    extraction only: responses carry our entry's fields, other-server counts
    and statuses — never whole config files (§8).
    """
    dependencies = [Depends(_local_token_dependency(token))] if token else []

    @app.get("/api/mcp-clients", response_model=ClientListResponse, dependencies=dependencies)
    async def list_clients() -> ClientListResponse:
        statuses = await service.list_status()
        return ClientListResponse(
            clients=[_to_client_status(status) for status in statuses],
            display_labels=dict(HANDSHAKE_LABELS),
        )

    @app.get(
        "/api/mcp-clients/{client_id}",
        response_model=ClientStatusResponse,
        dependencies=dependencies,
    )
    async def client_status(client_id: str) -> ClientStatusResponse:
        return _to_client_status(await service.status(client_id))

    @app.post(
        "/api/mcp-clients/{client_id}/install",
        response_model=ClientOperationResponse,
        dependencies=dependencies,
    )
    async def install_client(
        client_id: str, body: ClientOperationRequest | None = None
    ) -> ClientOperationResponse:
        force = body.force if body is not None else False
        result = await service.install(client_id, force=force)
        return _to_operation_response(result)

    @app.post(
        "/api/mcp-clients/{client_id}/verify",
        response_model=ClientOperationResponse,
        dependencies=dependencies,
    )
    async def verify_client(client_id: str) -> ClientOperationResponse:
        return _to_operation_response(await service.verify(client_id))

    @app.post(
        "/api/mcp-clients/{client_id}/remove",
        response_model=ClientOperationResponse,
        dependencies=dependencies,
    )
    async def remove_client(
        client_id: str, body: ClientRemoveRequest | None = None
    ) -> ClientOperationResponse:
        confirm = body.confirm if body is not None else False
        return _to_operation_response(
            await service.remove(client_id, confirm=confirm)
        )

    @app.get(
        "/api/mcp-clients/{client_id}/backups",
        response_model=BackupListResponse,
        dependencies=dependencies,
    )
    async def client_backups(client_id: str) -> BackupListResponse:
        items = await service.backups(client_id)
        return BackupListResponse(
            items=[
                BackupInfoResponse(
                    name=info.name,
                    created_at=info.created_at,
                    size_bytes=info.size_bytes,
                    sha256=info.sha256,
                    was_absent=info.was_absent,
                    matches_current=info.matches_current,
                )
                for info in items
            ]
        )

    @app.post(
        "/api/mcp-clients/{client_id}/restore",
        response_model=ClientOperationResponse,
        dependencies=dependencies,
    )
    async def restore_client(
        client_id: str, body: ClientRestoreRequest | None = None
    ) -> ClientOperationResponse:
        if body is None or not body.backup_name:
            # A restore without a named backup has no confirmation target —
            # the confirmation gate is the honest failure, not a code outside
            # the frozen table.
            raise HistoryApiError(
                422, "CONFIRMATION_REQUIRED", "restore requires backup_name and confirm:true"
            )
        result = await service.restore(
            client_id, body.backup_name, confirm=body.confirm
        )
        return _to_operation_response(result)

    @app.get(
        "/api/mcp-clients/{client_id}/config",
        response_model=CopyableConfigResponse,
        dependencies=dependencies,
    )
    async def client_copyable_config(client_id: str) -> CopyableConfigResponse:
        copyable = service.copyable(client_id)
        return CopyableConfigResponse(
            client_id=copyable.client_id,
            format=copyable.format,
            config_text=copyable.config_text,
            target_path=copyable.target_path,
            steps=copyable.steps,
        )


def _to_operation_response(result: OperationResult) -> ClientOperationResponse:
    """Re-shape the service's frozen OperationResult into the API model."""
    return ClientOperationResponse(
        client_id=result.client_id,
        operation=result.operation,
        performed=result.performed,
        installed=result.installed,
        entry_matches_current=result.entry_matches_current,
        handshake=result.handshake,
        handshake_reason=result.handshake_reason,
        handshake_checked_at=result.handshake_checked_at,
        backup_name=result.backup_name,
        restored_from=result.restored_from,
        safety_backup=result.safety_backup,
        removed_target=result.removed_target,
        copyable=(
            None
            if result.copyable is None
            else CopyableConfigResponse(
                client_id=result.copyable.client_id,
                format=result.copyable.format,
                config_text=result.copyable.config_text,
                target_path=result.copyable.target_path,
                steps=result.copyable.steps,
            )
        ),
        message=result.message,
    )
