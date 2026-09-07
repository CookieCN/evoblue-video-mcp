"""Production Local Engine assembly and FastAPI worker lifecycle."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import FastAPI
from platformdirs import user_data_path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.application.client_config.service import (
    ClientConfigService,
    default_payload,
    kernel_verifier,
)
from evoblue_video_mcp.asr.registration import register_available_asr_providers
from evoblue_video_mcp.asr.service import ModelManagerService, reconcile_waiting_asr_jobs
from evoblue_video_mcp.config import CredentialStore, KeyringCredentialStore, Settings
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.llm.http import HttpLLMProvider
from evoblue_video_mcp.mcp.engine_client import EngineClient
from evoblue_video_mcp.platforms.yt_dlp_adapter import YtDlpAdapter
from evoblue_video_mcp.reports.writer import ReportWriter
from evoblue_video_mcp.runtime.assembly import build_handlers
from evoblue_video_mcp.runtime.engine import recover_on_startup
from evoblue_video_mcp.runtime.worker import StageHandler, run_worker_once
from evoblue_video_mcp.storage import build_engine, init_db
from evoblue_video_mcp.storage.artifact_store import ArtifactStore
from evoblue_video_mcp.storage.models import AppSettings
from evoblue_video_mcp.storage.rebuild import IndexRebuildService
from evoblue_video_mcp.storage.repository import LeaseLostError, get_app_settings
from evoblue_video_mcp.web.app import create_app

HandlerFactory = Callable[
    [AppSettings | None], Awaitable[Mapping[JobStatus, StageHandler] | None]
]


@dataclass(frozen=True)
class RuntimePaths:
    """Resolved filesystem roots owned by the Local Engine."""

    data: Path
    database: Path
    artifacts: Path
    default_reports: Path


def resolve_runtime_paths(settings: Settings) -> RuntimePaths:
    """Resolve platform-standard paths, with a test/development override."""
    data = (
        settings.data_directory.resolve()
        if settings.data_directory is not None
        else Path(user_data_path("EvoBlue Video MCP", "EvoBlue")).resolve()
    )
    return RuntimePaths(
        data=data,
        database=data / "evoblue.db",
        artifacts=data / "artifacts",
        default_reports=data / "reports",
    )


class ProductionHandlerFactory:
    """Build handlers from the latest non-secret settings and keyring secret."""

    def __init__(
        self,
        paths: RuntimePaths,
        credentials: CredentialStore,
        models_dir: Path | None = None,
    ) -> None:
        self._paths = paths
        self._credentials = credentials
        self._models_dir = models_dir or paths.data / "models"

    async def __call__(
        self, settings: AppSettings | None
    ) -> Mapping[JobStatus, StageHandler] | None:
        if (
            settings is None
            or not settings.setup_completed
            or not settings.llm_provider
            or not settings.llm_base_url
            or not settings.llm_model
            or not settings.llm_credential_ref
        ):
            return None
        register_available_asr_providers(
            self._models_dir, settings.whisper_cpp_executable
        )
        try:
            api_key = await asyncio.to_thread(
                self._credentials.get_secret, settings.llm_credential_ref
            )
        except Exception:
            return None
        if not api_key:
            return None

        report_root = Path(settings.report_directory or self._paths.default_reports)
        return build_handlers(
            adapter=YtDlpAdapter(),
            artifact_store=ArtifactStore(self._paths.artifacts),
            report_writer=ReportWriter(report_root),
            llm=HttpLLMProvider(
                base_url=settings.llm_base_url,
                api_key=api_key,
                model=settings.llm_model,
            ),
            asr_provider_id=settings.asr_provider or "auto",
        )


async def run_runtime_worker_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: float,
    idle_sleep: float,
    stop: asyncio.Event,
    handler_factory: HandlerFactory,
) -> None:
    """Reload configuration before each claim and process jobs until shutdown."""
    while not stop.is_set():
        async with session_factory() as session:
            app_settings = await get_app_settings(session)
        handlers = await handler_factory(app_settings)
        job = None
        if handlers is not None:
            try:
                job = await run_worker_once(
                    session_factory,
                    owner=owner,
                    lease_seconds=lease_seconds,
                    handlers=handlers,
                )
            except LeaseLostError:
                continue
        if job is None:
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=idle_sleep)


def create_runtime_app(
    settings: Settings | None = None,
    *,
    credential_store: CredentialStore | None = None,
    handler_factory: HandlerFactory | None = None,
) -> FastAPI:
    """Create the production app with migrations, recovery, and worker lifecycle."""
    runtime_settings = settings or Settings()
    paths = resolve_runtime_paths(runtime_settings)
    paths.data.mkdir(parents=True, exist_ok=True)
    model_dir = runtime_settings.asr_model_dir or paths.data / "models"
    register_available_asr_providers(model_dir)
    engine = build_engine(paths.database)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    credentials = credential_store or KeyringCredentialStore()
    handlers = handler_factory or ProductionHandlerFactory(paths, credentials, Path(model_dir))
    model_service = ModelManagerService(models_dir=model_dir, session_factory=session_factory)
    report_pointer_file = paths.data / "report-root.txt"
    index_rebuild_service = IndexRebuildService(
        session_factory,
        # Deleted-database self-heal (§4): the settings row (and its
        # report_directory) dies with SQLite; recovery relies on
        # out-of-database persistence — the pointer file (kept in sync by
        # settings PUT) plus the platform reports dir as final fallback.
        fallback_report_root=paths.default_reports,
        pointer_file=report_pointer_file,
    )
    # P5 client-config service: payload from this process's interpreter
    # (contract §3), engine liveness via the same loopback client shape the
    # Bridge uses (trust_env=False so proxies cannot fake "online").
    probe_http = httpx.AsyncClient(trust_env=False)
    engine_probe_client = EngineClient(
        probe_http,
        base_url=f"http://{runtime_settings.engine_host}:{runtime_settings.engine_port}",
        token=runtime_settings.local_access_token or None,
    )

    async def _engine_online_probe() -> bool | None:
        try:
            return await engine_probe_client.health() is not None
        except httpx.TransportError:
            return False

    client_config_service = ClientConfigService(
        payload=default_payload(runtime_settings.engine_port),
        verifier=kernel_verifier(),
        engine_probe=_engine_online_probe,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        del app
        await init_db(engine)
        await model_service.cleanup_trash()
        await recover_on_startup(session_factory, now=time.time())
        # A previous run may have crashed between committing a model install and
        # resuming its waiting jobs; re-account them now that migrations and
        # provider registration are done.
        async with session_factory() as session:
            await reconcile_waiting_asr_jobs(
                session, models_dir=model_dir, now=time.time()
            )
        # Index reconciliation (§4): orphaned rebuild states, needs-rebuild
        # self-heal, and the first-boot backfill run in the background so the
        # API serves while the scan proceeds.
        reconcile_task = asyncio.create_task(
            index_rebuild_service.reconcile_on_startup()
        )
        stop = asyncio.Event()
        worker = asyncio.create_task(
            run_runtime_worker_loop(
                session_factory,
                owner=runtime_settings.worker_owner,
                lease_seconds=runtime_settings.worker_lease_seconds,
                idle_sleep=runtime_settings.worker_idle_sleep,
                stop=stop,
                handler_factory=handlers,
            )
        )
        try:
            yield
        finally:
            stop.set()
            worker.cancel()
            reconcile_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            with suppress(asyncio.CancelledError):
                await reconcile_task
            await index_rebuild_service.shutdown()
            await model_service.close()
            await engine.dispose()
            await probe_http.aclose()

    return create_app(
        runtime_settings,
        session_factory=session_factory,
        credential_store=credentials,
        model_service=model_service,
        index_rebuild_service=index_rebuild_service,
        report_pointer_file=report_pointer_file,
        fallback_report_root=paths.default_reports,
        data_directory=paths.data,
        client_config_service=client_config_service,
        lifespan=lifespan,
    )
