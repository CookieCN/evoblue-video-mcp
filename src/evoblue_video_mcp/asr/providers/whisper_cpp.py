"""Optional whisper.cpp CLI adapter; the base package ships neither CLI nor weights."""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from evoblue_video_mcp.asr.base import (
    ASR_CANCELLED,
    ASR_TRANSCRIPTION_FAILED,
    ASRCapabilities,
    ASRError,
    ASRRequest,
    ASRResult,
    ASRSegment,
    CancelCheck,
    ProgressCallback,
    SegmentCallback,
    segment_key,
)


class WhisperCppProvider:
    provider_id = "whisper-cpp-base"

    def __init__(self, executable: str | Path, model_path: str | Path) -> None:
        self._executable = str(executable)
        self._model_path = str(model_path)

    async def inspect(self) -> ASRCapabilities:
        return ASRCapabilities(
            provider_id=self.provider_id,
            model_id="whisper-cpp-base",
            model_version="80da2d8",
            languages=frozenset({"*"}),
            platforms=frozenset({"windows-x86_64", "macos-x86_64", "macos-arm64", "linux-x86_64"}),
        )

    async def transcribe(
        self,
        request: ASRRequest,
        *,
        is_cancelled: CancelCheck | None = None,
        on_progress: ProgressCallback | None = None,
        on_segment: SegmentCallback | None = None,
        resume_keys: frozenset[str] | None = None,
    ) -> ASRResult:
        if is_cancelled is not None and await is_cancelled():
            raise ASRError(ASR_CANCELLED, "transcription cancelled")
        with tempfile.TemporaryDirectory() as tmpdir:
            output_base = Path(tmpdir) / "transcript"
            command = [
                self._executable, "-m", self._model_path, "-f", request.audio_path,
                "-oj", "-of", str(output_base), "-np", "-l", request.language or "auto",
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                raise ASRError(ASR_TRANSCRIPTION_FAILED, "whisper.cpp CLI unavailable") from exc

            communicate = asyncio.create_task(process.communicate())
            while not communicate.done():
                if is_cancelled is not None and await is_cancelled():
                    process.kill()
                    await communicate
                    raise ASRError(ASR_CANCELLED, "transcription cancelled")
                await asyncio.sleep(0.1)
            _stdout, _stderr = await communicate
            if process.returncode != 0:
                raise ASRError(ASR_TRANSCRIPTION_FAILED, "whisper.cpp transcription failed")
            try:
                payload: dict[str, Any] = json.loads(
                    output_base.with_suffix(".json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise ASRError(ASR_TRANSCRIPTION_FAILED, "invalid whisper.cpp output") from exc

        resume = resume_keys or frozenset()
        segments: list[ASRSegment] = []
        for item in payload.get("transcription", []):
            timestamps = item.get("timestamps", {})
            segment = ASRSegment(
                start=_timestamp_seconds(str(timestamps.get("from", ""))),
                end=_timestamp_seconds(str(timestamps.get("to", ""))),
                text=str(item.get("text", "")).strip(),
            )
            if not segment.text or segment_key(segment.start, segment.end) in resume:
                continue
            if on_segment is not None:
                await on_segment(segment)
            segments.append(segment)
        if on_progress is not None:
            await on_progress(1.0)
        return ASRResult(
            segments=tuple(segments),
            detected_language=str(payload.get("result", {}).get("language", "")),
            provider_id=self.provider_id,
            model_id="whisper-cpp-base",
            model_version="80da2d8",
        )


def _timestamp_seconds(value: str) -> float:
    try:
        hours, minutes, seconds = value.replace(",", ".").split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError) as exc:
        raise ASRError(ASR_TRANSCRIPTION_FAILED, "invalid whisper.cpp timestamp") from exc
