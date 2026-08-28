"""Optional whisper.cpp provider invokes the CLI without a shell and normalizes JSON."""

import json
from pathlib import Path

from evoblue_video_mcp.asr.base import ASRRequest
from evoblue_video_mcp.asr.providers import whisper_cpp as module
from evoblue_video_mcp.asr.providers.whisper_cpp import WhisperCppProvider


async def test_whisper_cpp_parses_language_and_absolute_segments(tmp_path, monkeypatch) -> None:
    captured: list[str] = []

    class Process:
        returncode = 0

        async def communicate(self):
            output = Path(captured[captured.index("-of") + 1]).with_suffix(".json")
            output.write_text(
                json.dumps(
                    {
                        "result": {"language": "fr"},
                        "transcription": [
                            {
                                "timestamps": {
                                    "from": "00:00:01,250",
                                    "to": "00:00:03,500",
                                },
                                "text": " bonjour ",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return b"", b""

        def kill(self) -> None:
            raise AssertionError("process should not be killed")

    async def create(*args, **kwargs):
        captured.extend(str(arg) for arg in args)
        assert "shell" not in kwargs
        return Process()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", create)
    provider = WhisperCppProvider(tmp_path / "whisper-cli.exe", tmp_path / "ggml-base.bin")
    result = await provider.transcribe(ASRRequest(audio_path=str(tmp_path / "audio.wav")))

    assert result.detected_language == "fr"
    assert result.segments[0].start == 1.25
    assert result.segments[0].end == 3.5
    assert result.segments[0].text == "bonjour"
    assert "-oj" in captured
    assert "auto" in captured
