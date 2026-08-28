"""sherpa-onnx ASR provider for the Lite Zipformer and Standard SenseVoice tiers.

This is the only module allowed to import ``sherpa_onnx``. It loads one model
family per instance, segments long audio with the silero VAD, transcribes each
speech segment, and returns normalized ``ASRSegment`` output with absolute
timestamps.
"""

import asyncio
import importlib.util
import os
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

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


def _prepare_windows_onnxruntime_dll() -> object | None:
    """Prefer the ASR environment's ONNX Runtime over Windows' obsolete system DLL.

    Some Windows installations expose an old ``onnxruntime.dll`` from System32.
    The sherpa extension is loaded before Python can correct that DLL search, so
    it otherwise binds to the old C API and exits with a misleading
    ``version [N] is not supported`` message before reading any model file.
    """
    if sys.platform != "win32":
        return None

    spec = importlib.util.find_spec("onnxruntime")
    locations = spec.submodule_search_locations if spec is not None else None
    if not locations:
        raise ImportError(
            "onnxruntime>=1.27 is required with sherpa-onnx on Windows "
            "to avoid the obsolete System32 ONNX Runtime DLL"
        )

    capi_dir = Path(next(iter(locations))) / "capi"
    runtime_dll = capi_dir / "onnxruntime.dll"
    if not runtime_dll.is_file():
        raise ImportError(f"ONNX Runtime DLL is missing from {capi_dir}")
    return os.add_dll_directory(str(capi_dir))


# Retain the handle for the module lifetime; closing it removes the directory.
_ORT_DLL_DIRECTORY_HANDLE = _prepare_windows_onnxruntime_dll()

import sherpa_onnx  # type: ignore[import-untyped]  # noqa: E402

_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class SherpaModelSpec:
    """A sherpa-onnx model family and its on-disk artifacts."""

    provider_id: str
    family: str  # "zipformer_ctc" | "sense_voice"
    model_id: str
    model_version: str
    model_path: str  # *.onnx
    tokens_path: str  # tokens.txt
    languages: frozenset[str]
    num_threads: int = 1
    use_itn: bool = False


class SherpaOnnxProvider:
    """Loads one sherpa-onnx model and transcribes segmented audio."""

    def __init__(self, spec: SherpaModelSpec, vad_model_path: str) -> None:
        self.provider_id = spec.provider_id
        self._spec = spec
        self._vad_model_path = vad_model_path
        self._recognizer = self._load_recognizer(spec)

    def _load_recognizer(self, spec: SherpaModelSpec) -> Any:
        if spec.family == "zipformer_ctc":
            return sherpa_onnx.OfflineRecognizer.from_zipformer_ctc(
                model=spec.model_path,
                tokens=spec.tokens_path,
                num_threads=spec.num_threads,
                sample_rate=_SAMPLE_RATE,
            )
        if spec.family == "sense_voice":
            return sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=spec.model_path,
                tokens=spec.tokens_path,
                num_threads=spec.num_threads,
                sample_rate=_SAMPLE_RATE,
                use_itn=spec.use_itn,
            )
        raise ValueError(f"unknown sherpa-onnx family: {spec.family!r}")

    async def inspect(self) -> ASRCapabilities:
        return ASRCapabilities(
            provider_id=self.provider_id,
            model_id=self._spec.model_id,
            model_version=self._spec.model_version,
            languages=self._spec.languages,
            platforms=frozenset({"windows-x86_64"}),
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
        try:
            speech_segments = await asyncio.to_thread(self._load_and_segment, request.audio_path)
        except ASRError:
            raise
        except Exception as exc:
            raise ASRError(
                ASR_TRANSCRIPTION_FAILED, "audio segmentation failed", retryable=False
            ) from exc

        if not speech_segments:
            return self._result(())

        resume = resume_keys or frozenset()
        total = len(speech_segments)
        segments: list[ASRSegment] = []
        detected_language = ""
        for index, (start, end, samples) in enumerate(speech_segments):
            if is_cancelled is not None and await is_cancelled():
                raise ASRError(ASR_CANCELLED, "transcription cancelled")
            if on_progress is not None:
                await on_progress((index + 1) / total)
            if segment_key(start, end) in resume:
                continue
            text, lang = await asyncio.to_thread(self._transcribe_segment, samples)
            if not detected_language and lang:
                detected_language = lang
            segment = ASRSegment(start=start, end=end, text=text.strip())
            if not segment.text:
                continue
            if on_segment is not None:
                await on_segment(segment)
            segments.append(segment)

        return self._result(tuple(segments), detected_language)

    def _result(
        self, segments: tuple[ASRSegment, ...], detected_language: str = ""
    ) -> ASRResult:
        return ASRResult(
            segments=segments,
            detected_language=detected_language,
            provider_id=self.provider_id,
            model_id=self._spec.model_id,
            model_version=self._spec.model_version,
        )

    def _load_and_segment(self, path: str) -> list[tuple[float, float, NDArray[np.float32]]]:
        segments: list[tuple[float, float, NDArray[np.float32]]] = []
        with wave.open(path, "rb") as handle:
            if handle.getnchannels() != 1:
                raise ASRError(
                    ASR_TRANSCRIPTION_FAILED, "audio must be mono", retryable=False
                )
            if handle.getsampwidth() != 2:
                raise ASRError(
                    ASR_TRANSCRIPTION_FAILED, "audio must be 16-bit PCM", retryable=False
                )
            sample_rate = handle.getframerate()
            if sample_rate != _SAMPLE_RATE:
                raise ASRError(
                    ASR_TRANSCRIPTION_FAILED,
                    f"unsupported sample rate {sample_rate}",
                    retryable=False,
                )

            config = sherpa_onnx.VadModelConfig()
            config.silero_vad.model = self._vad_model_path
            config.silero_vad.threshold = 0.25
            config.silero_vad.min_silence_duration = 0.5
            config.silero_vad.min_speech_duration = 0.25
            config.silero_vad.window_size = 512
            config.sample_rate = sample_rate

            vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)
            while raw := handle.readframes(config.silero_vad.window_size):
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                vad.accept_waveform(samples)
                _drain_vad(vad, sample_rate, segments)

            # File input has no later chunk to close the final active speech span.
            vad.flush()
            _drain_vad(vad, sample_rate, segments)
        return segments

    def _transcribe_segment(self, samples: NDArray[np.float32]) -> tuple[str, str]:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(_SAMPLE_RATE, samples)
        self._recognizer.decode_stream(stream)
        result = stream.result
        text = getattr(result, "text", "")
        lang = getattr(result, "lang", "")
        return str(text), str(lang)
def _drain_vad(
    vad: Any,
    sample_rate: int,
    output: list[tuple[float, float, NDArray[np.float32]]],
) -> None:
    while not vad.empty():
        # ``front`` is a pybind property, and ``start`` is a sample offset.
        segment = vad.front
        samples = np.asarray(segment.samples, dtype=np.float32)
        start = float(segment.start) / sample_rate
        end = start + len(samples) / sample_rate
        output.append((start, end, samples))
        vad.pop()
