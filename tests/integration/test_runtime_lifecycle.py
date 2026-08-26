"""Production app lifecycle starts and stops the persistent worker."""

import asyncio
import json

import httpx

from evoblue_video_mcp.config import Settings
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
from evoblue_video_mcp.storage.artifact_store import ArtifactStore
from evoblue_video_mcp.storage.models import AppSettings


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
    assert len(handlers) == 7
