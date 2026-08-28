"""Model install orchestration: turn a manifest into an installed, active model.

``install_model`` binds the download/install primitives to the repository state
machine. It is crash-safe and idempotent:

* a non-releasable manifest is refused before any state is written;
* a per-model lock ensures only one executor drives an operation at a time;
* an already-active version short-circuits to its completed operation;
* the partial file lives at a stable (model, version) path so a retry after a
  network failure inherits and resumes it, with validators persisted as soon as
  the response headers arrive;
* install record, active pointer, and completed status commit atomically;
* any exception transitions the operation to ``failed``/``cancelled`` with a
  stable, desensitized error code, keeps the previous active version untouched,
  and preserves a resumable partial while discarding an untrustworthy one.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from evoblue_video_mcp.asr.manifest import ModelManifest, is_releasable
from evoblue_video_mcp.asr.model_manager import (
    ArchiveError,
    DigestMismatchError,
    DownloadError,
    DownloadOutcome,
    SizeMismatchError,
    _file_sha256,
    download_resumable,
    install_archive,
)
from evoblue_video_mcp.storage.model_download import (
    TERMINAL_DOWNLOAD_STATUSES,
    ModelDownloadStatus,
)
from evoblue_video_mcp.storage.models import ModelDownload
from evoblue_video_mcp.storage.repository import (
    advance_download_operation,
    complete_installation,
    create_download_operation,
    find_active_download_operation,
    find_latest_download_operation,
    get_active_model,
    get_download_operation,
    get_installation,
    restart_download,
    switch_download_source,
    update_download_progress,
)

# Stable, desensitized error codes surfaced on ModelDownload.error_code.
NOT_RELEASABLE = "NOT_RELEASABLE"
INSTALL_IN_PROGRESS = "INSTALL_IN_PROGRESS"
INSTALL_CONFLICT = "INSTALL_CONFLICT"
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
CANCELLED = "CANCELLED"
INTERNAL_ERROR = "INTERNAL"

# Persist progress at most once per this many new bytes (plus a final flush).
_PROGRESS_INTERVAL = 4 * 1024 * 1024

# Per-model locks serialize concurrent install requests within one process.
_locks: dict[str, asyncio.Lock] = {}


class NotReleasableError(ValueError):
    code = NOT_RELEASABLE


class InstallConflictError(ValueError):
    code = INSTALL_IN_PROGRESS


class _Cancelled(Exception):
    """Internal signal that cancellation was requested mid-operation."""


def _model_lock(model_id: str) -> asyncio.Lock:
    lock = _locks.get(model_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[model_id] = lock
    return lock


def _temp_path(model_id: str, version: str, models_root: Path) -> Path:
    # Stable per (model, version) so a retry after failure/crash reuses the same
    # partial instead of orphaning it under a fresh operation id. Hierarchical so
    # distinct tuples cannot collide: ("a-b", "c") and ("a", "b-c") would share
    # "a-b-c.part" under flat naming but land in different directories here.
    return models_root / ".downloads" / model_id / f"{version}.part"


def _truncate(path: Path) -> None:
    with suppress(FileNotFoundError):
        path.unlink()


def _verify_downloaded(temp_path: Path, expected_size_bytes: int, expected_sha256: str) -> None:
    """Re-verify a completed archive file by size and streaming digest."""
    if not temp_path.exists():
        raise SizeMismatchError(f"downloaded archive missing: {temp_path}")
    actual = temp_path.stat().st_size
    if actual != expected_size_bytes:
        raise SizeMismatchError(f"downloaded size {actual} != expected {expected_size_bytes}")
    if _file_sha256(temp_path) != expected_sha256:
        raise DigestMismatchError("downloaded sha256 does not match expected digest")


async def _get_or_create_operation(
    session: AsyncSession,
    *,
    manifest: ModelManifest,
    source_url: str,
    source_kind: str,
    source_sha256: str,
    source_size_bytes: int,
    operation_id: str | None,
    now: Callable[[], float],
) -> ModelDownload:
    active = await find_active_download_operation(session, model_id=manifest.model_id)
    if active is not None:
        if active.version != manifest.version:
            raise InstallConflictError(
                f"an install for {manifest.model_id!r} v{active.version} is already in progress"
            )
        return active

    op_id = operation_id or uuid.uuid4().hex
    prior = await find_latest_download_operation(
        session, model_id=manifest.model_id, version=manifest.version
    )
    try:
        return await create_download_operation(
            session,
            operation_id=op_id,
            model_id=manifest.model_id,
            version=manifest.version,
            source_url=source_url,
            source_kind=source_kind,
            expected_sha256=source_sha256,
            expected_size_bytes=source_size_bytes,
            now=now(),
            etag=prior.etag if prior else None,
            last_modified=prior.last_modified if prior else None,
        )
    except IntegrityError:
        # A concurrent request created the operation first; reuse it instead of 500.
        await session.rollback()
        existing = await find_active_download_operation(session, model_id=manifest.model_id)
        if existing is None:
            raise
        return existing


async def _already_active(
    session: AsyncSession, *, model_id: str, version: str
) -> ModelDownload | None:
    """Return the completed operation when the version is already installed and active."""
    active = await get_active_model(session, model_id=model_id)
    if active is None or active.active_version != version:
        return None
    if await get_installation(session, model_id=model_id, version=version) is None:
        return None
    latest = await find_latest_download_operation(session, model_id=model_id, version=version)
    if latest is not None and latest.status == ModelDownloadStatus.COMPLETED.value:
        return latest
    return None


async def _mark_terminal(
    session: AsyncSession,
    *,
    operation_id: str,
    to_status: ModelDownloadStatus,
    now: Callable[[], float],
    error_code: str | None = None,
) -> ModelDownload:
    # A prior commit may have failed (e.g. disk/SQLite error) and left the session
    # in pending-rollback; clear it so the terminal write can still be attempted.
    # If the database is persistently unavailable this is best-effort only.
    with suppress(Exception):
        await session.rollback()
    op = await get_download_operation(session, operation_id=operation_id)
    if op is None:
        raise RuntimeError(f"download operation {operation_id!r} vanished")
    if ModelDownloadStatus(op.status) in TERMINAL_DOWNLOAD_STATUSES:
        return op
    return await advance_download_operation(
        session,
        operation_id=operation_id,
        to_status=to_status,
        now=now(),
        error_code=error_code,
    )


async def install_model(
    *,
    session: AsyncSession,
    manifest: ModelManifest,
    http_client: httpx.AsyncClient,
    models_dir: str | Path,
    operation_id: str | None = None,
    now: Callable[[], float] = time.time,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
) -> ModelDownload:
    """Install ``manifest``'s model, returning the terminal operation on success or failure."""
    if not is_releasable(manifest):
        raise NotReleasableError(
            f"manifest {manifest.model_id!r}/{manifest.version!r} is not releasable"
        )
    async with _model_lock(manifest.model_id):
        return await _install_model_locked(
            session=session,
            manifest=manifest,
            http_client=http_client,
            models_dir=models_dir,
            operation_id=operation_id,
            now=now,
            cancel_check=cancel_check,
        )


async def _install_model_locked(
    *,
    session: AsyncSession,
    manifest: ModelManifest,
    http_client: httpx.AsyncClient,
    models_dir: str | Path,
    operation_id: str | None,
    now: Callable[[], float],
    cancel_check: Callable[[], Awaitable[bool]] | None,
) -> ModelDownload:
    existing = await _already_active(
        session, model_id=manifest.model_id, version=manifest.version
    )
    if existing is not None:
        return existing

    primary = manifest.sources[0]
    # Every source shares one SHA-256 and size (enforced by the manifest contract),
    # so the partial file and these expected values are source-agnostic.
    expected_sha256 = primary.sha256
    expected_size = primary.size_bytes
    models_root = Path(models_dir)

    op = await _get_or_create_operation(
        session,
        manifest=manifest,
        source_url=primary.url,
        source_kind=primary.kind,
        source_sha256=expected_sha256,
        source_size_bytes=expected_size,
        operation_id=operation_id,
        now=now,
    )
    op_id = op.operation_id
    temp_path = _temp_path(manifest.model_id, manifest.version, models_root)

    state = {"last_persisted": op.downloaded_bytes}

    async def persist_progress(n: int) -> None:
        if cancel_check is not None and await cancel_check():
            raise _Cancelled()
        if n - state["last_persisted"] >= _PROGRESS_INTERVAL or n == expected_size:
            await update_download_progress(
                session, operation_id=op_id, downloaded_bytes=n, now=now()
            )
            state["last_persisted"] = n

    async def on_restart() -> None:
        state["last_persisted"] = 0
        await restart_download(session, operation_id=op_id, now=now())

    async def on_headers(etag: str | None, last_modified: str | None) -> None:
        await update_download_progress(
            session,
            operation_id=op_id,
            downloaded_bytes=state["last_persisted"],
            etag=etag,
            last_modified=last_modified,
            temp_path=str(temp_path),
            now=now(),
        )

    try:
        status = ModelDownloadStatus(op.status)

        if status is ModelDownloadStatus.PENDING:
            op = await advance_download_operation(
                session,
                operation_id=op_id,
                to_status=ModelDownloadStatus.DOWNLOADING,
                now=now(),
                temp_path=str(temp_path),
            )
            status = ModelDownloadStatus.DOWNLOADING

        if status is ModelDownloadStatus.DOWNLOADING:
            actual = temp_path.stat().st_size if temp_path.exists() else 0
            if actual > expected_size:
                # Oversized / corrupt partial: cannot be trusted.
                _truncate(temp_path)
                op = await restart_download(session, operation_id=op_id, now=now())
            elif actual > op.downloaded_bytes:
                # The file is ahead of the last checkpoint (e.g. a crash between
                # progress writes): trust the file and resume from its length.
                op = await update_download_progress(
                    session,
                    operation_id=op_id,
                    downloaded_bytes=actual,
                    temp_path=str(temp_path),
                    now=now(),
                )
            elif actual < op.downloaded_bytes:
                # The file is behind the recorded progress: inconsistent, restart.
                _truncate(temp_path)
                op = await restart_download(session, operation_id=op_id, now=now())
            state["last_persisted"] = op.downloaded_bytes

            outcome: DownloadOutcome | None = None
            for index, source in enumerate(manifest.sources):
                try:
                    outcome = await download_resumable(
                        source.url,
                        temp_path,
                        source.sha256,
                        source.size_bytes,
                        http_client,
                        progress=persist_progress,
                        on_restart=on_restart,
                        on_headers=on_headers,
                        etag=op.etag,
                        last_modified=op.last_modified,
                    )
                    break
                except (DownloadError, httpx.HTTPError):
                    if index + 1 >= len(manifest.sources):
                        raise
                    # A failed source may have left a corrupt byte prefix; without
                    # per-chunk hashes a cross-source resume is unsafe, so restart
                    # from zero at the next source.
                    _truncate(temp_path)
                    state["last_persisted"] = 0
                    op = await switch_download_source(
                        session,
                        operation_id=op_id,
                        source_url=manifest.sources[index + 1].url,
                        source_kind=manifest.sources[index + 1].kind,
                        now=now(),
                    )
            if outcome is None:
                raise DownloadError("all download sources failed")
            await update_download_progress(
                session, operation_id=op_id, downloaded_bytes=expected_size, now=now()
            )
            op = await advance_download_operation(
                session,
                operation_id=op_id,
                to_status=ModelDownloadStatus.VERIFYING,
                now=now(),
                etag=outcome.etag,
                last_modified=outcome.last_modified,
            )
            status = ModelDownloadStatus.VERIFYING

        if status is ModelDownloadStatus.VERIFYING:
            _verify_downloaded(temp_path, expected_size, expected_sha256)
            op = await advance_download_operation(
                session,
                operation_id=op_id,
                to_status=ModelDownloadStatus.INSTALLING,
                now=now(),
            )
            status = ModelDownloadStatus.INSTALLING

        if status is ModelDownloadStatus.INSTALLING:
            version_dir = models_root / manifest.model_id / manifest.version
            installed_path = install_archive(temp_path, manifest, version_dir)
            op = await complete_installation(
                session,
                operation_id=op_id,
                model_id=manifest.model_id,
                version=manifest.version,
                installed_path=installed_path,
                now=now(),
            )

        return op
    except _Cancelled:
        current = await get_download_operation(session, operation_id=op_id)
        if current is not None and current.status == ModelDownloadStatus.DOWNLOADING.value:
            actual = temp_path.stat().st_size if temp_path.exists() else 0
            await update_download_progress(
                session, operation_id=op_id, downloaded_bytes=actual, now=now()
            )
        return await _mark_terminal(
            session,
            operation_id=op_id,
            to_status=ModelDownloadStatus.CANCELLED,
            now=now,
            error_code=CANCELLED,
        )
    except DownloadError as exc:
        _truncate(temp_path)
        return await _mark_terminal(
            session,
            operation_id=op_id,
            to_status=ModelDownloadStatus.FAILED,
            now=now,
            error_code=exc.code,
        )
    except ArchiveError as exc:
        _truncate(temp_path)
        return await _mark_terminal(
            session,
            operation_id=op_id,
            to_status=ModelDownloadStatus.FAILED,
            now=now,
            error_code=exc.code,
        )
    except FileExistsError:
        return await _mark_terminal(
            session,
            operation_id=op_id,
            to_status=ModelDownloadStatus.FAILED,
            now=now,
            error_code=INSTALL_CONFLICT,
        )
    except httpx.HTTPError:
        return await _mark_terminal(
            session,
            operation_id=op_id,
            to_status=ModelDownloadStatus.FAILED,
            now=now,
            error_code=DOWNLOAD_FAILED,
        )
    except Exception:
        return await _mark_terminal(
            session,
            operation_id=op_id,
            to_status=ModelDownloadStatus.FAILED,
            now=now,
            error_code=INTERNAL_ERROR,
        )
