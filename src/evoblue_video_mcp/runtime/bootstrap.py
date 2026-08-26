"""Production Local Engine assembly and FastAPI worker lifecycle."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from platformdirs import user_data_path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.config import CredentialStore, KeyringCredentialStore, Settings
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.llm.http import HttpLLMProvider
from evoblue_video_mcp.platforms.yt_dlp_adapter import YtDlpAdapter
from evoblue_video_mcp.reports.writer import ReportWriter
from evoblue_video_mcp.runtime.assembly import build_handlers
from evoblue_video_mcp.runtime.engine import recover_on_startup
from evoblue_video_mcp.runtime.worker import StageHandler, run_worker_once
from evoblue_video_mcp.storage import build_engine, init_db
from evoblue_video_mcp.storage.artifact_store import ArtifactStore
from evoblue_video_mcp.storage.models import AppSettings
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

    def __init__(self, paths: RuntimePaths, credentials: CredentialStore) -> None:
        self._paths = paths
        self._credentials = credentials

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
    engine = build_engine(paths.database)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    credentials = credential_store or KeyringCredentialStore()
    handlers = handler_factory or ProductionHandlerFactory(paths, credentials)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        del app
        await init_db(engine)
        await recover_on_startup(session_factory, now=time.time())
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
            with suppress(asyncio.CancelledError):
                await worker
            await engine.dispose()

    return create_app(
        runtime_settings,
        session_factory=session_factory,
        credential_store=credentials,
        lifespan=lifespan,
    )
