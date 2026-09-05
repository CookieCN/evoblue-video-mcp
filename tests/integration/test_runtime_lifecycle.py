"""Production app lifecycle starts and stops the persistent worker."""

import asyncio
import json

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from evoblue_video_mcp.asr.providers.whisper_cpp import WhisperCppProvider
from evoblue_video_mcp.asr.registration import reset_managed_registrations
from evoblue_video_mcp.asr.registry import clear, get_provider
from evoblue_video_mcp.config import Settings
from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.platforms.models import (
    Transcript,
    TranscriptSegment,
    VideoMetadata,
    VideoRef,
)
from evoblue_video_mcp.reports.writer import ReportWriter
from evoblue_video_mcp.runtime.assembly import build_handlers
from evoblue_video_mcp.runtime.bootstrap import (
    ProductionHandlerFactory,
    RuntimePaths,
    create_runtime_app,
)
from evoblue_video_mcp.storage import build_engine, init_db
from evoblue_video_mcp.storage.artifact_store import ArtifactStore
from evoblue_video_mcp.storage.models import AppSettings
from evoblue_video_mcp.storage.repository import (
    enqueue_job,
    get_job,
    save_app_settings,
    save_installation,
)


class _MemoryCredentials:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    def get_secret(self, reference: str) -> str | None:
        return self.values.get(reference)

    def set_secret(self, reference: str, secret: str) -> None:
        self.values[reference] = secret

    def delete_secret(self, reference: str) -> None:
        self.values.pop(reference, None)


class _Adapter:
    def supports(self, ref: VideoRef) -> bool:
        return True

    async def fetch_metadata(self, ref: VideoRef) -> VideoMetadata:
        return VideoMetadata(
            video_id=ref.video_id,
            platform=ref.platform,
            title="Lifecycle Test",
            author="EvoBlue",
        )

    async def fetch_transcript(self, ref: VideoRef) -> Transcript:
        return Transcript(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="hello runtime")],
            language="en",
            source="fixture",
        )


class _LLM:
    async def complete(self, prompt: str) -> str:
        if "JSON object" in prompt:
            return json.dumps(
                {
                    "core_summary": "runtime summary",
                    "key_takeaways": ["worker lifecycle is active"],
                    "timeline_outline": "- Runtime",
                    "content_analysis": "The pipeline completed through the app lifecycle.",
                }
            )
        return "chunk summary"


async def test_runtime_lifecycle_drives_submitted_job_to_report(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    report_writer = ReportWriter(tmp_path / "reports")

    async def handler_factory(app_settings):
        return build_handlers(
            adapter=_Adapter(),
            artifact_store=artifact_store,
            report_writer=report_writer,
            llm=_LLM(),
        )

    app = create_runtime_app(
        Settings(data_directory=tmp_path / "data", worker_idle_sleep=0.01),
        credential_store=_MemoryCredentials(),
        handler_factory=handler_factory,
    )

    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        submitted = await client.post(
            "/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"}
        )
        assert submitted.status_code == 200
        job_id = submitted.json()["job_id"]

        for _ in range(100):
            detail = await client.get(f"/api/jobs/{job_id}")
            if detail.json()["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("runtime worker did not complete the submitted job")

    reports = list((tmp_path / "reports").glob("*.md"))
    assert len(reports) == 1
    assert "runtime summary" in reports[0].read_text(encoding="utf-8")


async def test_production_factory_builds_real_handler_set_without_network(tmp_path) -> None:
    paths = RuntimePaths(
        data=tmp_path,
        database=tmp_path / "db.sqlite",
        artifacts=tmp_path / "artifacts",
        default_reports=tmp_path / "reports",
    )
    credentials = _MemoryCredentials({"llm:openai": "secret"})
    factory = ProductionHandlerFactory(paths, credentials)
    settings = AppSettings(
        id=1,
        setup_completed=True,
        llm_provider="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-test",
        llm_credential_ref="llm:openai",
        updated_at=1.0,
    )

    handlers = await factory(settings)
    assert handlers is not None
    assert len(handlers) == 9


async def test_startup_rediscovers_whisper_provider_and_resumes_stranded_job(
    tmp_path,
) -> None:
    # Previous run: the whisper model was installed and the CLI configured, but
    # the process died before the waiting job was resumed. Restarting must
    # rediscover the provider from disk + settings with an empty registry —
    # no pre-registered fakes — and re-account the stranded job.
    clear()
    reset_managed_registrations()

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    models_dir = data_dir / "models"
    model_file = models_dir / "whisper-cpp-base" / "80da2d8" / "ggml-base.bin"
    model_file.parent.mkdir(parents=True)
    model_file.write_bytes(b"ggml")
    cli_file = data_dir / "whisper-cli.exe"
    cli_file.write_bytes(b"stub cli")

    engine: AsyncEngine = build_engine(data_dir / "evoblue.db")
    await init_db(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await save_installation(
            session,
            model_id="whisper-cpp-base",
            version="80da2d8",
            installed_path=str(models_dir / "whisper-cpp-base"),
            now=1.0,
        )
        await save_app_settings(
            session,
            setup_completed=True,
            now=1.0,
            whisper_cpp_executable=str(cli_file),
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
        job.asr_recommendation_model_id = "whisper-cpp-base"
        await session.commit()
    await engine.dispose()

    app = create_runtime_app(
        Settings(data_directory=data_dir, worker_idle_sleep=0.01),
        credential_store=_MemoryCredentials(),
    )
    async with app.router.lifespan_context(app):
        # Inside the lifespan the provider exists — and it is the real
        # whisper.cpp adapter built from the on-disk model and CLI setting.
        assert isinstance(get_provider("whisper-cpp-base"), WhisperCppProvider)

    engine = build_engine(data_dir / "evoblue.db")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        stored = await get_job(session, job_id="stranded-1")
    await engine.dispose()

    assert stored is not None
    assert stored.status == JobStatus.TRANSCRIBING.value
    assert stored.asr_recommendation_model_id is None
    clear()
    reset_managed_registrations()


async def test_provider_load_failure_does_not_block_engine_or_other_tiers(
    tmp_path, monkeypatch
) -> None:
    # A corrupt sherpa model must not take the engine down: registration runs
    # before the lifespan, so a raising model load is isolated to its tier —
    # it stays unregistered and its waiting job keeps waiting, while a healthy
    # tier (whisper) still registers and resumes its stranded job.
    clear()
    reset_managed_registrations()

    def boom(**kwargs):
        raise RuntimeError("corrupt weights")

    monkeypatch.setattr("evoblue_video_mcp.asr.registration._build_sherpa", boom)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    models_dir = data_dir / "models"
    whisper_model = models_dir / "whisper-cpp-base" / "80da2d8" / "ggml-base.bin"
    whisper_model.parent.mkdir(parents=True)
    whisper_model.write_bytes(b"ggml")
    standard_dir = models_dir / "sensevoice-small-int8" / "2024-07-17"
    standard_dir.mkdir(parents=True)
    (standard_dir / "model.int8.onnx").write_bytes(b"weights")
    (standard_dir / "tokens.txt").write_bytes(b"tokens")
    cli_file = data_dir / "whisper-cli.exe"
    cli_file.write_bytes(b"stub cli")

    engine: AsyncEngine = build_engine(data_dir / "evoblue.db")
    await init_db(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await save_installation(
            session,
            model_id="whisper-cpp-base",
            version="80da2d8",
            installed_path=str(models_dir / "whisper-cpp-base"),
            now=1.0,
        )
        await save_installation(
            session,
            model_id="sensevoice-small-int8",
            version="2024-07-17",
            installed_path=str(models_dir / "sensevoice-small-int8"),
            now=1.0,
        )
        await save_app_settings(
            session,
            setup_completed=True,
            now=1.0,
            whisper_cpp_executable=str(cli_file),
        )
        pinned = (
            ("waiting-whisper", "whisper-cpp-base"),
            ("waiting-sherpa", "sensevoice-small-int8"),
        )
        for job_id, model_id in pinned:
            job = await enqueue_job(
                session,
                job_id=job_id,
                url="https://youtu.be/dQw4w9WgXcQ",
                request_fingerprint=f"fp-{job_id}",
                config_fingerprint="cfg",
                status=JobStatus.WAITING_FOR_MODEL,
                now=1.0,
            )
            job.asr_recommendation_model_id = model_id
            # enqueue_job owns its transaction (BEGIN IMMEDIATE §6): pending
            # caller changes must be resolved BEFORE the next writer runs.
            await session.commit()
    await engine.dispose()

    app = create_runtime_app(
        Settings(data_directory=data_dir, worker_idle_sleep=0.01),
        credential_store=_MemoryCredentials(),
    )
    async with app.router.lifespan_context(app):
        # The engine came up; the failing tier is simply not there.
        assert isinstance(get_provider("whisper-cpp-base"), WhisperCppProvider)
        assert get_provider("sherpa-onnx-standard") is None

    engine = build_engine(data_dir / "evoblue.db")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        whisper_job = await get_job(session, job_id="waiting-whisper")
        sherpa_job = await get_job(session, job_id="waiting-sherpa")
    await engine.dispose()

    assert whisper_job is not None
    assert whisper_job.status == JobStatus.TRANSCRIBING.value
    assert sherpa_job is not None
    assert sherpa_job.status == JobStatus.WAITING_FOR_MODEL.value
    assert sherpa_job.asr_recommendation_model_id == "sensevoice-small-int8"
    clear()
    reset_managed_registrations()
