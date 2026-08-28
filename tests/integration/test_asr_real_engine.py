"""Real sherpa-onnx SenseVoice end-to-end (requires downloaded models).

These are the ASR-1 proof-of-capability tests and are skipped when the model
artifacts are absent.
"""

import shutil
import wave
from pathlib import Path

import numpy as np
import pytest
from platformdirs import user_data_path

from evoblue_video_mcp.asr.base import ASRError, ASRRequest, ASRSegment, segment_key
from evoblue_video_mcp.asr.providers.sherpa_onnx import SherpaModelSpec, SherpaOnnxProvider
from evoblue_video_mcp.asr.registration import (
    register_available_asr_providers,
    reset_managed_registrations,
)
from evoblue_video_mcp.asr.registry import clear, get_provider

MODEL_DIR = Path(user_data_path("EvoBlue Video MCP", "EvoBlue")) / "models"
STANDARD_DIR = MODEL_DIR / "sensevoice-small-int8"
LITE_DIR = MODEL_DIR / "zipformer-ctc-small-zh-int8"
VAD_MODEL = MODEL_DIR / "silero_vad.onnx"
TEST_WAV = MODEL_DIR / "lei-jun-test.wav"

_HAS_MODEL = (STANDARD_DIR / "model.int8.onnx").exists() and VAD_MODEL.exists()
_HAS_LITE = (LITE_DIR / "model.int8.onnx").exists() and VAD_MODEL.exists()


def _provider() -> SherpaOnnxProvider:
    return SherpaOnnxProvider(
        SherpaModelSpec(
            provider_id="sherpa-onnx-standard",
            family="sense_voice",
            model_id="sensevoice-small-int8",
            model_version="2024-07-17",
            model_path=str(STANDARD_DIR / "model.int8.onnx"),
            tokens_path=str(STANDARD_DIR / "tokens.txt"),
            languages=frozenset({"zh", "en", "ja", "ko", "yue"}),
        ),
        vad_model_path=str(VAD_MODEL),
    )


def _lite_provider() -> SherpaOnnxProvider:
    return SherpaOnnxProvider(
        SherpaModelSpec(
            provider_id="sherpa-onnx-lite",
            family="zipformer_ctc",
            model_id="zipformer-ctc-small-zh-int8",
            model_version="2025-07-16",
            model_path=str(LITE_DIR / "model.int8.onnx"),
            tokens_path=str(LITE_DIR / "tokens.txt"),
            languages=frozenset({"zh"}),
        ),
        vad_model_path=str(VAD_MODEL),
    )


def _write_silence(path: Path, seconds: float) -> None:
    rate = 16000
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))


@pytest.mark.skipif(not _HAS_MODEL, reason="SenseVoice int8 model not installed")
async def test_sensevoice_transcribes_silence_without_hallucination(tmp_path) -> None:
    provider = _provider()
    wav = tmp_path / "silence.wav"
    _write_silence(wav, 3.0)
    result = await provider.transcribe(ASRRequest(audio_path=str(wav)))
    assert result.segments == ()


@pytest.mark.skipif(not _HAS_MODEL or not TEST_WAV.exists(), reason="model or test wav missing")
async def test_sensevoice_transcribes_speech_with_monotonic_timestamps() -> None:
    provider = _provider()
    result = await provider.transcribe(ASRRequest(audio_path=str(TEST_WAV)))
    assert result.segments
    starts = [s.start for s in result.segments]
    assert starts == sorted(starts)
    assert all(s.end >= s.start for s in result.segments)


@pytest.mark.skipif(not _HAS_LITE or not TEST_WAV.exists(), reason="model or test wav missing")
async def test_zipformer_lite_transcribes_real_speech() -> None:
    provider = _lite_provider()
    result = await provider.transcribe(ASRRequest(audio_path=str(TEST_WAV)))
    assert result.segments
    assert all(segment.text.strip() for segment in result.segments)
    assert all(segment.end >= segment.start for segment in result.segments)


def _write_synthetic_long_audio(path: Path, seconds: int, speech_wav: Path) -> None:
    rate = 16000
    with wave.open(str(speech_wav), "rb") as handle:
        speech = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    repeats = (rate * seconds) // len(speech) + 1
    tiled = np.tile(speech, repeats)[: rate * seconds]
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(tiled.tobytes())


@pytest.mark.skipif(not _HAS_MODEL or not TEST_WAV.exists(), reason="model or test wav missing")
def test_sensevoice_segments_60_minutes_monotonic_bounded(tmp_path) -> None:
    provider = _provider()
    wav = tmp_path / "long.wav"
    _write_synthetic_long_audio(wav, seconds=3600, speech_wav=TEST_WAV)
    segments = provider._load_and_segment(str(wav))
    assert segments
    starts = [s[0] for s in segments]
    assert starts == sorted(starts)
    assert all(0.0 <= s[0] <= 3600.0 and 0.0 <= s[1] <= 3600.0 for s in segments)
    assert all(s[1] >= s[0] for s in segments)


@pytest.mark.skipif(not _HAS_MODEL or not TEST_WAV.exists(), reason="model or test wav missing")
async def test_sensevoice_cancel_then_resume_from_checkpoint() -> None:
    provider = _provider()
    checks = {"count": 0}
    collected: list[ASRSegment] = []

    async def is_cancelled() -> bool:
        checks["count"] += 1
        return checks["count"] > 2

    async def on_segment(segment: ASRSegment) -> None:
        collected.append(segment)

    with pytest.raises(ASRError) as exc:
        await provider.transcribe(
            ASRRequest(audio_path=str(TEST_WAV)),
            is_cancelled=is_cancelled,
            on_segment=on_segment,
        )
    assert exc.value.error_code == "ASR_CANCELLED"
    assert collected

    resume_keys = frozenset(segment_key(s.start, s.end) for s in collected)
    result = await provider.transcribe(
        ASRRequest(audio_path=str(TEST_WAV)), resume_keys=resume_keys
    )
    assert result.segments
    assert all(s.end >= s.start for s in result.segments)


@pytest.mark.skipif(not _HAS_MODEL or not TEST_WAV.exists(), reason="model or test wav missing")
async def test_registration_from_clean_model_dir_yields_transcribable_provider(
    tmp_path,
) -> None:
    # A clean models directory containing only the recognition archive files
    # (as Model Manager leaves them — the archives ship no VAD) must produce a
    # working provider: the VAD comes from the bundled pinned asset, and the
    # result must survive a real transcription.
    clear()
    reset_managed_registrations()

    clean_dir = tmp_path / "models"
    tier_dir = clean_dir / "sensevoice-small-int8" / "2024-07-17"
    tier_dir.mkdir(parents=True)
    shutil.copy2(STANDARD_DIR / "model.int8.onnx", tier_dir / "model.int8.onnx")
    shutil.copy2(STANDARD_DIR / "tokens.txt", tier_dir / "tokens.txt")

    register_available_asr_providers(clean_dir)
    provider = get_provider("sherpa-onnx-standard")
    assert isinstance(provider, SherpaOnnxProvider)

    result = await provider.transcribe(ASRRequest(audio_path=str(TEST_WAV)))
    assert result.segments
    assert all(segment.end >= segment.start for segment in result.segments)

    clear()
    reset_managed_registrations()
