"""ModelManagerService: list/install/cancel/uninstall over the built-in catalog."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.asr import service as service_module
from evoblue_video_mcp.asr.fake import FakeASRProvider
from evoblue_video_mcp.asr.registry import clear, register_provider
from evoblue_video_mcp.asr.service import ModelManagerService, reconcile_waiting_asr_jobs
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.storage.repository import (
    create_download_operation,
    enqueue_job,
    get_app_settings,
    get_job,
    save_app_settings,
    save_installation,
)


def _register_standard_provider() -> None:
    """A fake provider standing in for an installed sherpa-onnx standard tier."""
    provider = FakeASRProvider(
        model_id="sensevoice-small-int8",
        model_version="2024-07-17",
        languages=frozenset({"zh", "en", "ja", "ko", "yue"}),
    )
    provider.provider_id = "sherpa-onnx-standard"
    register_provider(provider)


def _service(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> ModelManagerService:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ModelManagerService(
        models_dir=tmp_path, session_factory=session_factory, http_client=client
    )


async def test_list_models_reports_builtin_models(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    service = _service(session_factory, tmp_path)
    try:
        models = await service.list_models()
    finally:
        await service.close()

    ids = {m.model_id for m in models}
    assert ids == {
        "sensevoice-small-int8",
        "qwen3-asr-0.6b-int8",
        "whisper-cpp-base",
        "zipformer-ctc-small-zh-int8",
    }
    for model in models:
        assert model.installed is False
        assert model.active is False
        assert model.status is None
        assert model.compressed_size_bytes > 0
        assert model.installed_size_bytes > 0
        assert model.redistribution in {"upstream_only", "mirror_approved"}


async def test_install_unknown_model_raises(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    service = _service(session_factory, tmp_path)
    try:
        with pytest.raises(KeyError):
            await service.install("unknown")
    finally:
        await service.close()


async def test_cancel_unknown_model_raises(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    service = _service(session_factory, tmp_path)
    try:
        with pytest.raises(KeyError):
            await service.cancel("unknown")
    finally:
        await service.close()


async def test_uninstall_nothing_reclaims_zero(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    service = _service(session_factory, tmp_path)
    try:
        result = await service.uninstall("sensevoice-small-int8")
    finally:
        await service.close()
    assert result.reclaimed_bytes == 0
    assert result.pending_reclaim_bytes == 0


async def test_uninstall_refuses_while_download_in_flight(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    async with session_factory() as session:
        await create_download_operation(
            session,
            operation_id="op-1",
            model_id="sensevoice-small-int8",
            version="2024-07-17",
            source_url="https://github.com/x.tar.bz2",
            source_kind="upstream",
            expected_sha256="a" * 64,
            expected_size_bytes=100,
            now=1.0,
        )

    service = _service(session_factory, tmp_path)
    try:
        with pytest.raises(RuntimeError):
            await service.uninstall("sensevoice-small-int8")
    finally:
        await service.close()


async def test_install_launches_background_install(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    calls: dict[str, int] = {}

    async def fake_install_model(
        session: AsyncSession,
        *,
        manifest,
        http_client,
        models_dir,
        cancel_check,
    ):
        calls["n"] = calls.get("n", 0) + 1
        return SimpleNamespace(status="completed")

    monkeypatch.setattr(service_module, "install_model", fake_install_model)
    service = _service(session_factory, tmp_path)
    try:
        summary = await service.install("zipformer-ctc-small-zh-int8")
        await asyncio.sleep(0.01)
        assert calls.get("n") == 1
        assert summary.model_id == "zipformer-ctc-small-zh-int8"
    finally:
        await service.close()


async def test_successful_install_resumes_jobs_waiting_for_that_model(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    clear()
    _register_standard_provider()

    async def fake_install_model(session, *, manifest, **kwargs):
        # Mirror the real installer: the install record is committed before
        # the resume runs, and only a completed operation resumes anything.
        await save_installation(
            session,
            model_id=manifest.model_id,
            version=manifest.version,
            installed_path=str(tmp_path / manifest.model_id),
            now=1.0,
        )
        return SimpleNamespace(status="completed")

    monkeypatch.setattr(service_module, "install_model", fake_install_model)

    async with session_factory() as session:
        job = await enqueue_job(
            session,
            job_id="waiting-1",
            url="https://youtu.be/dQw4w9WgXcQ",
            request_fingerprint="fp",
            config_fingerprint="cfg",
            status=JobStatus.WAITING_FOR_MODEL,
            now=1.0,
        )
        job.asr_recommendation_model_id = "sensevoice-small-int8"
        await session.commit()

    service = _service(session_factory, tmp_path)
    try:
        await service.install("sensevoice-small-int8")
        await asyncio.sleep(0.01)
    finally:
        await service.close()

    async with session_factory() as session:
        resumed = await get_job(session, job_id="waiting-1")
    assert resumed is not None
    assert resumed.status == JobStatus.TRANSCRIBING.value
    assert resumed.asr_recommendation_model_id is None


async def test_install_completion_does_not_resume_without_ready_provider(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    # whisper model installed per SQLite, but no CLI configured: the provider
    # gate must keep the job parked instead of resuming it into a dead end.
    clear()

    async def fake_install_model(session, *, manifest, **kwargs):
        await save_installation(
            session,
            model_id=manifest.model_id,
            version=manifest.version,
            installed_path=str(tmp_path / manifest.model_id),
            now=1.0,
        )
        return SimpleNamespace(status="completed")

    monkeypatch.setattr(service_module, "install_model", fake_install_model)

    async with session_factory() as session:
        job = await enqueue_job(
            session,
            job_id="waiting-cli",
            url="https://youtu.be/dQw4w9WgXcQ",
            request_fingerprint="fp",
            config_fingerprint="cfg",
            status=JobStatus.WAITING_FOR_MODEL,
            now=1.0,
        )
        job.asr_recommendation_model_id = "whisper-cpp-base"
        await session.commit()

    service = _service(session_factory, tmp_path)
    try:
        await service.install("whisper-cpp-base")
        await asyncio.sleep(0.01)
    finally:
        await service.close()

    async with session_factory() as session:
        parked = await get_job(session, job_id="waiting-cli")
    assert parked is not None
    assert parked.status == JobStatus.WAITING_FOR_MODEL.value


async def test_reconcile_resumes_after_whisper_cli_configured(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    # Model installed first, CLI configured later: reconciliation must refresh
    # provider registration from settings and then resume the parked job.
    clear()
    cli_path = tmp_path / "whisper-cli.exe"
    cli_path.write_bytes(b"stub")
    model_file = tmp_path / "whisper-cpp-base" / "80da2d8" / "ggml-base.bin"
    model_file.parent.mkdir(parents=True)
    model_file.write_bytes(b"ggml")

    async with session_factory() as session:
        await save_installation(
            session,
            model_id="whisper-cpp-base",
            version="80da2d8",
            installed_path=str(tmp_path / "whisper-cpp-base"),
            now=1.0,
        )
        job = await enqueue_job(
            session,
            job_id="waiting-whisper",
            url="https://youtu.be/dQw4w9WgXcQ",
            request_fingerprint="fp",
            config_fingerprint="cfg",
            status=JobStatus.WAITING_FOR_MODEL,
            now=1.0,
        )
        job.asr_recommendation_model_id = "whisper-cpp-base"
        await session.commit()

    service = _service(session_factory, tmp_path)
    try:
        # Before the CLI exists in settings there is nothing to resume into.
        assert await service.reconcile_waiting() == []
        async with session_factory() as session:
            parked = await get_job(session, job_id="waiting-whisper")
            assert parked is not None
            assert parked.status == JobStatus.WAITING_FOR_MODEL.value

            await save_app_settings(
                session,
                setup_completed=True,
                now=2.0,
                whisper_cpp_executable=str(cli_path),
            )
            current = await get_app_settings(session)
            assert current is not None

        assert await service.reconcile_waiting() == ["waiting-whisper"]
    finally:
        await service.close()
        clear()

    async with session_factory() as session:
        resumed = await get_job(session, job_id="waiting-whisper")
    assert resumed is not None
    assert resumed.status == JobStatus.TRANSCRIBING.value
    assert resumed.asr_recommendation_model_id is None


async def test_reconcile_recovers_jobs_stranded_by_crash_before_resume(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    # Crash window: the install record was committed but the process died
    # before the waiting job was resumed. Startup reconciliation closes it.
    clear()
    _register_standard_provider()

    async with session_factory() as session:
        await save_installation(
            session,
            model_id="sensevoice-small-int8",
            version="2024-07-17",
            installed_path=str(tmp_path / "sensevoice-small-int8"),
            now=1.0,
        )
        job = await enqueue_job(
            session,
            job_id="stranded-1",
            url="https://youtu.be/dQw4w9WgXcQ",
            request_fingerprint="fp",
            config_fingerprint="cfg",
            status=JobStatus.WAITING_FOR_MODEL,
            now=1.0,
        )
        job.asr_recommendation_model_id = "sensevoice-small-int8"
        await session.commit()

    async with session_factory() as session:
        resumed = await reconcile_waiting_asr_jobs(
            session, models_dir=tmp_path, now=2.0
        )
    assert resumed == ["stranded-1"]

    async with session_factory() as session:
        stored = await get_job(session, job_id="stranded-1")
    assert stored is not None
    assert stored.status == JobStatus.TRANSCRIBING.value


async def test_uninstall_removes_files_and_reports_reclaimed(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    model_dir = tmp_path / "sensevoice-small-int8"
    model_dir.mkdir(parents=True)
    (model_dir / "model.int8.onnx").write_bytes(b"weights-123")

    service = _service(session_factory, tmp_path)
    try:
        result = await service.uninstall("sensevoice-small-int8")
    finally:
        await service.close()

    assert result.reclaimed_bytes == len(b"weights-123")
    assert result.pending_reclaim_bytes == 0
    assert not model_dir.exists()


async def test_uninstall_reports_pending_reclaim_when_trash_delete_fails(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    model_dir = tmp_path / "sensevoice-small-int8"
    model_dir.mkdir(parents=True)
    (model_dir / "model.int8.onnx").write_bytes(b"weights")

    def boom(path, *args, **kwargs) -> None:
        # Only the trash deletion (no ignore_errors) should fail; the best-effort
        # partial cleanup passes ignore_errors=True and must stay swallowed.
        if not kwargs.get("ignore_errors"):
            raise OSError("file locked")

    monkeypatch.setattr(service_module.shutil, "rmtree", boom)
    service = _service(session_factory, tmp_path)
    try:
        result = await service.uninstall("sensevoice-small-int8")
    finally:
        await service.close()

    # The rename still happened (atomic), so the dir is no longer at its old path;
    # the failed trash deletion is reported as pending reclaim, not freed space.
    assert result.reclaimed_bytes == 0
    assert result.pending_reclaim_bytes == len(b"weights")
    assert not model_dir.exists()


async def test_close_leaves_injected_client_open(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = ModelManagerService(
        models_dir=tmp_path, session_factory=session_factory, http_client=client
    )
    await service.close()
    assert not client.is_closed
    await client.aclose()


async def test_uninstall_restores_dir_when_db_delete_fails(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    model_dir = tmp_path / "sensevoice-small-int8"
    model_dir.mkdir(parents=True)
    (model_dir / "model.int8.onnx").write_bytes(b"weights")

    async def boom(session: AsyncSession, *, model_id: str) -> None:
        raise RuntimeError("db commit failed")

    monkeypatch.setattr(service_module, "uninstall_model", boom)
    service = _service(session_factory, tmp_path)
    try:
        with pytest.raises(RuntimeError):
            await service.uninstall("sensevoice-small-int8")
    finally:
        await service.close()

    # The DB delete failed, so the moved directory is restored to its original
    # location: files and records stay consistent (still "installed").
    assert model_dir.exists()
    assert (model_dir / "model.int8.onnx").read_bytes() == b"weights"


async def test_cleanup_trash_restores_when_record_present(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    # Simulate a crash between the atomic move and the DB delete: files are in
    # .trash but the install record still exists.
    trash_entry = tmp_path / ".trash" / "sensevoice-small-int8" / "abcd1234"
    trash_entry.mkdir(parents=True)
    (trash_entry / "model.int8.onnx").write_bytes(b"weights")

    async with session_factory() as session:
        await save_installation(
            session,
            model_id="sensevoice-small-int8",
            version="2024-07-17",
            installed_path=str(tmp_path / "sensevoice-small-int8"),
            now=1.0,
        )

    service = _service(session_factory, tmp_path)
    try:
        await service.cleanup_trash()
    finally:
        await service.close()

    assert (tmp_path / "sensevoice-small-int8" / "model.int8.onnx").read_bytes() == b"weights"
    assert not trash_entry.exists()


async def test_cleanup_trash_deletes_when_record_gone(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    trash_entry = tmp_path / ".trash" / "sensevoice-small-int8" / "abcd1234"
    trash_entry.mkdir(parents=True)
    (trash_entry / "model.int8.onnx").write_bytes(b"weights")

    service = _service(session_factory, tmp_path)
    try:
        await service.cleanup_trash()
    finally:
        await service.close()

    assert not (tmp_path / ".trash" / "sensevoice-small-int8").exists()
