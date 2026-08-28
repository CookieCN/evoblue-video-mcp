"""Model Manager service: the WebUI-facing facade over install/cancel/uninstall.

Holds the built-in manifests and an ``httpx.AsyncClient``, launches installs as
background tasks so a 163 MB download never blocks an HTTP request, and merges
persisted install/download state with the manifest for the model list. It never
imports concrete ASR engines; all acquisition goes through ``install_model``.
"""

import asyncio
import contextlib
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.asr.installer import install_model
from evoblue_video_mcp.asr.manifest import ModelManifest, is_releasable
from evoblue_video_mcp.asr.manifests import BUILTIN_MANIFESTS
from evoblue_video_mcp.asr.registration import (
    register_available_asr_providers,
    unregister_managed_provider,
)
from evoblue_video_mcp.asr.registry import get_provider
from evoblue_video_mcp.storage.repository import (
    find_active_download_operation,
    find_latest_download_operation,
    get_active_model,
    get_app_settings,
    get_installation,
    get_installations,
    list_waiting_model_ids,
    resume_waiting_jobs,
    uninstall_model,
)

logger = logging.getLogger(__name__)

_TIERS = {
    "zipformer-ctc-small-zh-int8": "lite",
    "sensevoice-small-int8": "standard",
    "whisper-cpp-base": "multilingual",
}

# Model id → provider id for the built-in catalog. Recommendations are only
# ever pinned for built-in models (custom providers are always reported
# installed), so this mapping covers every reachable
# ``asr_recommendation_model_id``; uninstall uses the same pairs to drop the
# engine registration. Unknown ids are left waiting, never resumed.
_MODEL_PROVIDER_IDS = {
    "zipformer-ctc-small-zh-int8": "sherpa-onnx-lite",
    "sensevoice-small-int8": "sherpa-onnx-standard",
    "whisper-cpp-base": "whisper-cpp-base",
}


@dataclass(frozen=True)
class ModelSummary:
    """One model's manifest fields merged with its persisted install/download state."""

    model_id: str
    version: str
    tier: str
    provider: str
    languages: tuple[str, ...]
    compressed_size_bytes: int
    installed_size_bytes: int
    license: str
    attribution: str
    redistribution: str
    installed: bool
    active: bool
    installed_path: str | None
    status: str | None
    downloaded_bytes: int
    error_code: str | None
    formal_default: bool


@dataclass(frozen=True)
class UninstallResult:
    """Outcome of an uninstall: actually reclaimed bytes vs deferred reclaim.

    ``reclaimed_bytes`` is disk space actually freed now; ``pending_reclaim_bytes``
    is space still held by ``.trash`` (a locked file) that startup reclaims later.
    Exactly one is non-zero unless the model was already absent.
    """

    reclaimed_bytes: int
    pending_reclaim_bytes: int


def _dir_size(path: Path) -> int:
    """Return the total byte size of every file under ``path`` (0 if missing)."""
    if not path.is_dir():
        return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


async def reconcile_waiting_asr_jobs(
    session: AsyncSession,
    *,
    models_dir: Path | None,
    now: float,
    retry_failed_loads: bool = False,
) -> list[str]:
    """Resume ``waiting_for_model`` jobs whose recommended model is fully ready.

    The single recovery entry point for parked jobs; every resume path goes
    through here. Both gates must pass before a job leaves the waiting state:
    an install record in SQLite *and* a registered provider. Provider
    registration is refreshed from current disk and settings state first, so
    one call covers all three events that can make a job resumable: a completed
    model install, engine startup (closing the crash window between "install
    committed" and "jobs resumed"), and saving a whisper.cpp CLI path in
    settings. ``retry_failed_loads`` re-attempts providers whose last load
    failed — an install or settings event may have fixed them. Unknown
    recommendation ids and not-yet-ready models are left waiting — never
    resumed blindly and never downloaded implicitly.
    """
    settings = await get_app_settings(session)
    register_available_asr_providers(
        models_dir,
        settings.whisper_cpp_executable if settings else None,
        retry_failed_loads=retry_failed_loads,
    )
    resumed: list[str] = []
    for model_id in await list_waiting_model_ids(session):
        provider_id = _MODEL_PROVIDER_IDS.get(model_id)
        if provider_id is None:
            logger.warning("no provider maps model %r; job stays waiting", model_id)
            continue
        installed = bool(await get_installations(session, model_id=model_id))
        if not installed or get_provider(provider_id) is None:
            logger.info("model %r not fully ready yet; jobs stay waiting", model_id)
            continue
        resumed.extend(await resume_waiting_jobs(session, model_id=model_id, now=now))
    return resumed


class ModelManagerService:
    """Launch and observe model installs, cancel them, and uninstall them."""

    def __init__(
        self,
        models_dir: str | Path,
        session_factory: async_sessionmaker[AsyncSession],
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._models_dir = Path(models_dir)
        self._session_factory = session_factory
        self._http_client = http_client or httpx.AsyncClient()
        self._owns_client = http_client is None
        self._manifests: dict[str, ModelManifest] = {
            manifest.model_id: manifest for manifest in BUILTIN_MANIFESTS
        }
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    def _require_manifest(self, model_id: str) -> ModelManifest:
        manifest = self._manifests.get(model_id)
        if manifest is None:
            raise KeyError(f"unknown model {model_id!r}")
        return manifest

    def _is_running(self, model_id: str) -> bool:
        task = self._tasks.get(model_id)
        return task is not None and not task.done()

    def _start(self, manifest: ModelManifest) -> None:
        model_id = manifest.model_id
        event = self._cancel_events.setdefault(model_id, asyncio.Event())
        event.clear()

        async def run() -> None:
            async def cancel_check() -> bool:
                return event.is_set()

            try:
                async with self._session_factory() as session:
                    operation = await install_model(
                        session=session,
                        manifest=manifest,
                        http_client=self._http_client,
                        models_dir=self._models_dir,
                        cancel_check=cancel_check,
                    )
                    if operation.status == "completed":
                        await reconcile_waiting_asr_jobs(
                            session,
                            models_dir=self._models_dir,
                            now=time.time(),
                            retry_failed_loads=True,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                # install_model already desensitizes its own failures; this catches
                # session/DB errors that escape it so a background task never dies
                # with an unretrieved exception.
                logger.exception("model install task failed: %s", model_id)
            finally:
                self._tasks.pop(model_id, None)

        self._tasks[model_id] = asyncio.create_task(run())

    async def _summary_for(
        self, session: AsyncSession, manifest: ModelManifest
    ) -> ModelSummary:
        model_id = manifest.model_id
        install = await get_installation(
            session, model_id=model_id, version=manifest.version
        )
        active = await get_active_model(session, model_id=model_id)
        op = await find_latest_download_operation(
            session, model_id=model_id, version=manifest.version
        )
        return ModelSummary(
            model_id=model_id,
            version=manifest.version,
            tier=_TIERS.get(model_id, "standard"),
            provider=manifest.provider,
            languages=manifest.languages,
            compressed_size_bytes=manifest.compressed_size_bytes,
            installed_size_bytes=manifest.installed_size_bytes,
            license=manifest.license,
            attribution=manifest.attribution,
            redistribution=manifest.redistribution,
            installed=install is not None,
            active=active is not None and active.active_version == manifest.version,
            installed_path=install.installed_path if install else None,
            status=op.status if op else None,
            downloaded_bytes=op.downloaded_bytes if op else 0,
            error_code=op.error_code if op else None,
            # ASR-4 benchmark/release gates are not complete; ASR-3 may emit an
            # automatic install recommendation without claiming formal approval.
            formal_default=False,
        )

    async def list_models(self) -> list[ModelSummary]:
        """Return every built-in model with its current install state."""
        async with self._session_factory() as session:
            return [await self._summary_for(session, m) for m in BUILTIN_MANIFESTS]

    async def reconcile_waiting(self, *, retry_failed_loads: bool = False) -> list[str]:
        """Resume waiting jobs that just became runnable; see module reconcile."""
        async with self._session_factory() as session:
            return await reconcile_waiting_asr_jobs(
                session,
                models_dir=self._models_dir,
                now=time.time(),
                retry_failed_loads=retry_failed_loads,
            )

    async def install(self, model_id: str) -> ModelSummary:
        """Start (or join) an install, returning the current state without blocking."""
        manifest = self._require_manifest(model_id)
        if not is_releasable(manifest):
            raise ValueError(f"model {model_id!r} is not releasable")
        if not self._is_running(model_id):
            self._start(manifest)
        async with self._session_factory() as session:
            return await self._summary_for(session, manifest)

    async def cancel(self, model_id: str) -> ModelSummary:
        """Signal a running download to stop at its next safe point."""
        manifest = self._require_manifest(model_id)
        event = self._cancel_events.get(model_id)
        if event is not None:
            event.set()
        async with self._session_factory() as session:
            return await self._summary_for(session, manifest)

    async def uninstall(self, model_id: str) -> UninstallResult:
        """Atomically remove the model's install/active/download records and files.

        Refuses while a download is in flight so an uninstall cannot race an
        activation. The installed directory is first moved to ``.trash`` (atomic,
        same filesystem); if the DB delete then fails the directory is restored in
        place. A leftover trash entry is reconciled against the DB on startup.
        """
        self._require_manifest(model_id)
        async with self._session_factory() as session:
            active_op = await find_active_download_operation(session, model_id=model_id)
            if active_op is not None:
                raise RuntimeError("model install in progress; cancel before uninstalling")

        model_dir = self._models_dir / model_id
        reclaimed = _dir_size(model_dir)

        # 1. Move the installed directory to .trash/<model_id>/<unique> atomically.
        trash_path: Path | None = None
        if model_dir.exists():
            trash_root = self._models_dir / ".trash" / model_id
            trash_root.mkdir(parents=True, exist_ok=True)
            trash_path = trash_root / uuid.uuid4().hex[:8]
            os.replace(model_dir, trash_path)

        # 2. Commit DB deletion; restore the files if the commit fails so records
        #    and files stay consistent (no "DB gone but files stranded" state).
        try:
            async with self._session_factory() as session:
                await uninstall_model(session, model_id=model_id)
        except Exception:
            if trash_path is not None:
                try:
                    os.replace(trash_path, model_dir)
                except OSError:
                    logger.exception(
                        "failed to restore model dir after DB delete error: %s", model_id
                    )
            raise

        # 3. Reclaim the trash best-effort; a locked file defers to startup.
        pending_reclaim = 0
        if trash_path is not None:
            try:
                shutil.rmtree(trash_path.parent)
            except OSError:
                pending_reclaim = reclaimed

        # 4. Drop the engine registration right away; the expected-state
        #    registration reconcile would also reach this on its next run, but
        #    a just-uninstalled model must not stay transcribable meanwhile.
        #    Ownership-checked: an external registration under the same id
        #    (tests, user code) is never removed here.
        provider_id = _MODEL_PROVIDER_IDS.get(model_id)
        if provider_id is not None:
            unregister_managed_provider(provider_id)

        # The partial is recreatable and not part of the installed footprint.
        shutil.rmtree(self._models_dir / ".downloads" / model_id, ignore_errors=True)

        return UninstallResult(
            reclaimed_bytes=0 if pending_reclaim else reclaimed,
            pending_reclaim_bytes=pending_reclaim,
        )

    async def cleanup_trash(self) -> None:
        """Reconcile ``.trash`` leftovers against the DB.

        A leftover whose model still has an install record means the process died
        between the atomic move and the DB delete: restore the files. A leftover
        whose DB record is gone is a failed deletion: reclaim it.
        """
        trash_root = self._models_dir / ".trash"
        if not trash_root.is_dir():
            return
        async with self._session_factory() as session:
            for model_entry in sorted(trash_root.iterdir()):
                if not model_entry.is_dir():
                    continue
                model_id = model_entry.name
                has_install = bool(await get_installations(session, model_id=model_id))
                if not has_install:
                    shutil.rmtree(model_entry, ignore_errors=True)
                    continue

                target = self._models_dir / model_id
                entries = sorted(p for p in model_entry.iterdir() if p.is_dir())
                for entry in entries:
                    if target.exists():
                        shutil.rmtree(entry, ignore_errors=True)  # stale
                    else:
                        try:
                            os.replace(entry, target)
                        except OSError:
                            logger.exception("failed to restore model from trash: %s", model_id)
                with contextlib.suppress(OSError):
                    model_entry.rmdir()

    async def close(self) -> None:
        """Cancel running installs and release a client we created."""
        for event in self._cancel_events.values():
            event.set()
        running = [task for task in self._tasks.values() if not task.done()]
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        if self._owns_client:
            await self._http_client.aclose()
