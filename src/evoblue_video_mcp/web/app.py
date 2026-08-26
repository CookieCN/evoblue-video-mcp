"""Local Engine FastAPI factory."""

import asyncio
import secrets
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Literal, TypedDict

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp import __version__
from evoblue_video_mcp.application.submit import compute_config_fingerprint, submit_video
from evoblue_video_mcp.config import CredentialStore, Settings, llm_credential_reference
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.platforms.detector import PlatformError
from evoblue_video_mcp.storage.models import Job
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
    JobDetailResponse,
    JobListItem,
    JobListResponse,
    SubmitJobInput,
    SubmitJobResponse,
)


class HealthResponse(TypedDict):
    status: Literal["ok"]
    service: str
    version: str


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
    )


def create_app(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    local_token: str | None = None,
    credential_store: CredentialStore | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """Create the Local Engine HTTP application.

    Data endpoints attach when a session factory is given. When ``local_token``
    (or ``Settings.local_access_token``) is set, data endpoints require it via the
    ``X-Local-Token`` header; ``/api/health`` stays token-exempt.
    """
    app_settings = settings or Settings()
    token = local_token if local_token is not None else app_settings.local_access_token
    if app_settings.environment == "production" and not token:
        raise RuntimeError("local access token is required in production")
    app = FastAPI(title=app_settings.app_name, version=__version__, lifespan=lifespan)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return {"status": "ok", "service": app_settings.app_name, "version": __version__}

    if session_factory is not None:
        _register_data_endpoints(app, session_factory, token, credential_store)

    return app


def _register_data_endpoints(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    token: str,
    credential_store: CredentialStore | None,
) -> None:
    async def credential_is_configured(reference: str | None) -> bool:
        if credential_store is None or reference is None:
            return False
        try:
            return bool(await asyncio.to_thread(credential_store.get_secret, reference))
        except Exception:
            return False

    async def require_local_token(x_local_token: str | None = Header(default=None)) -> None:
        if not token:
            return
        if x_local_token is None or not secrets.compare_digest(x_local_token, token):
            raise HTTPException(status_code=401, detail="invalid local access token")

    dependencies = [Depends(require_local_token)] if token else []

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
        async with session_factory() as sess:
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
            llm_api_key_configured=await credential_is_configured(current.llm_credential_ref),
        )

    @app.put("/api/settings", response_model=AppSettingsResponse, dependencies=dependencies)
    async def settings_put(payload: AppSettingsUpdate) -> AppSettingsResponse:
        async with session_factory() as sess:
            current = await get_app_settings(sess)
            fields = payload.model_fields_set
            provider = (
                payload.llm_provider
                if "llm_provider" in fields
                else (current.llm_provider if current else None)
            )
            credential_ref = current.llm_credential_ref if current else None
            if "llm_provider" in fields and provider != (current.llm_provider if current else None):
                credential_ref = llm_credential_reference(provider or "")
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
            saved = await save_app_settings(
                sess,
                setup_completed=(
                    payload.setup_completed
                    if payload.setup_completed is not None
                    else (current.setup_completed if current else False)
                ),
                now=time.time(),
                report_directory=(
                    payload.report_directory
                    if "report_directory" in fields
                    else (current.report_directory if current else None)
                ),
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
            )
        return AppSettingsResponse(
            setup_completed=saved.setup_completed,
            report_directory=saved.report_directory,
            llm_provider=saved.llm_provider,
            llm_base_url=saved.llm_base_url,
            llm_model=saved.llm_model,
            llm_api_key_configured=await credential_is_configured(saved.llm_credential_ref),
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
                    provider=provider, base_url=base_url, model=model
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
