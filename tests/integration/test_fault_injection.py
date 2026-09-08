"""P8-004 fault-injection regressions for real user-facing failure modes.

Each test pins a failure mode to its stable user-visible behaviour (exit code,
message, error code) so a refactor cannot silently turn it back into a Python
traceback. Rows of docs/HARDENING_MATRIX.md reference these tests by id; modes
already covered elsewhere are cited in the matrix, not duplicated here.
"""

import socket
import subprocess
import sys
from pathlib import Path

from evoblue_video_mcp.runtime import singleton


def _free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_port_occupied_at_boot_exits_4_with_guidance(tmp_path: Path) -> None:
    """FI-1: a busy port must produce exit 4 + the frozen message, not a traceback."""
    port = _free_port()
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    driver = (
        "import sys\n"
        "from evoblue_video_mcp.__main__ import main\n"
        "sys.argv = ['evoblue-engine-full']\n"
        "sys.exit(main())\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", driver],
            capture_output=True,
            text=True,
            timeout=60,
            env={
                "EVOBLUE_DATA_DIRECTORY": str(tmp_path / "data"),
                "EVOBLUE_ENGINE_PORT": str(port),
                "EVOBLUE_OPEN_UI": "0",
                "PATH": "",
                "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
            },
        )
        assert proc.returncode == singleton.EXIT_PORT_UNAVAILABLE
        assert "已被其他程序占用" in proc.stderr
        assert "Traceback" not in proc.stderr
    finally:
        blocker.close()


def test_second_instance_message_precedes_any_server_setup(
    tmp_path: Path, monkeypatch
) -> None:
    """FI-2: double-instance exit 3 happens before app construction."""
    # hermetic mutex: acquiring the real one would poison the whole pytest
    # process for every later test that spawns a child instance (children
    # share the session-local mutex namespace and would exit 3 on sight)
    monkeypatch.setattr(singleton, "acquire_mutex", lambda: object())
    port = _free_port()
    assert singleton.acquire(tmp_path, host="127.0.0.1", port=port, version="t") is None
    # same process: the live pid in the lock still conflicts -> exit 3
    code = singleton.acquire(tmp_path, host="127.0.0.1", port=port, version="t")
    assert code == singleton.EXIT_ALREADY_RUNNING
    message = singleton.conflict_message(code, port)
    assert "已在运行" in message




class _OkAdapter:
    """Minimal adapter that satisfies the metadata/subtitle stages."""

    def supports(self, ref) -> bool:
        return True

    async def fetch_metadata(self, ref):
        from evoblue_video_mcp.platforms.base import VideoMetadata

        return VideoMetadata(
            video_id=ref.video_id, platform=ref.platform, title="FI3", author="A"
        )

    async def fetch_transcript(self, ref):
        from evoblue_video_mcp.platforms.base import Transcript, TranscriptSegment

        return Transcript(
            segments=[TranscriptSegment(start=0.0, end=2.0, text="hello")],
            language="en",
            source="youtube-subtitle",
        )


class _JsonLLM:
    async def complete(self, prompt: str) -> str:
        import json

        if "JSON" in prompt:
            return json.dumps(
                {
                    "core_summary": "s",
                    "key_takeaways": ["t"],
                    "timeline_outline": "o",
                    "content_analysis": "a",
                }
            )
        return "chunk summary"


async def test_report_write_failure_fails_job_sanitized(
    tmp_path, session_factory
) -> None:
    """FI-3: an unwritable report root fails the job without leaking the OS error."""


    from evoblue_video_mcp.application.submit import submit_video
    from evoblue_video_mcp.jobs import JobStatus
    from evoblue_video_mcp.reports.writer import ReportWriter
    from evoblue_video_mcp.runtime.handlers import (
        ChunkingHandler,
        CleaningTranscriptHandler,
        FetchingMetadataHandler,
        FetchingSubtitlesHandler,
        GeneratingReportHandler,
        SummarizingChunksHandler,
    )
    from evoblue_video_mcp.runtime.worker import run_worker_once
    from evoblue_video_mcp.storage.artifact_store import ArtifactStore

    class BrokenWriter(ReportWriter):
        async def write_report(self, filename, markdown, previous_hash=None):
            raise OSError(13, "Permission denied")

    adapter = _OkAdapter()
    store = ArtifactStore(tmp_path / "artifacts")
    handlers = {
        JobStatus.FETCHING_METADATA: FetchingMetadataHandler(adapter),
        JobStatus.FETCHING_SUBTITLES: FetchingSubtitlesHandler(adapter, store),
        JobStatus.CLEANING_TRANSCRIPT: CleaningTranscriptHandler(store),
        JobStatus.CHUNKING: ChunkingHandler(store),
        JobStatus.SUMMARIZING_CHUNKS: SummarizingChunksHandler(store, _JsonLLM()),
        JobStatus.GENERATING_REPORT: GeneratingReportHandler(
            store, BrokenWriter(tmp_path / "reports"), _JsonLLM()
        ),
    }

    async with session_factory() as sess:
        _, reused = await submit_video(
            sess,
            url="https://youtu.be/FaultInject",
            config_fingerprint="cfg",
            now=1000.0,
            reuse_window_seconds=3600.0,
        )
        assert reused is False

    job = None
    for _ in range(12):
        job = await run_worker_once(
            session_factory, owner="fi", lease_seconds=30.0, now_fn=lambda: 1000.0,
            handlers=handlers,
        )
        if job is not None and job.status in {
            JobStatus.FAILED.value,
            JobStatus.COMPLETED.value,
        }:
            break
    assert job is not None and job.status == JobStatus.FAILED.value
    assert "Permission denied" not in (job.error_detail or "")
    assert "tmp_path" not in (job.error_detail or "")
