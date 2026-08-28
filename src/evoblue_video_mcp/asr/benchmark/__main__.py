"""Reproducible ASR benchmark scoring command.

Usage:
    python -m evoblue_video_mcp.asr.benchmark CORPUS_JSON [--provider fake]
        [--models-dir DIR] [--tier standard|lite] [--gate] [--out report.json]

The ``fake`` provider scores without model weights. The real sherpa-onnx tiers
(``--tier standard`` / ``--tier lite``) build providers through the same
registration path the Engine uses (model files + bundled VAD verification), so
a passing benchmark exercises the shipped loading chain, not a shortcut.
"""

import argparse
import asyncio
import json
import platform
import time
import wave
from pathlib import Path
from typing import Any

from evoblue_video_mcp.asr.base import ASRProvider, ASRRequest
from evoblue_video_mcp.asr.benchmark.manifest import BenchmarkCorpusManifest
from evoblue_video_mcp.asr.benchmark.metrics import (
    character_error_rate,
    entity_recall,
    normalize_for_scoring,
    normalize_words,
    word_error_rate,
)
from evoblue_video_mcp.asr.benchmark.resources import current_peak_rss
from evoblue_video_mcp.asr.fake import FakeASRProvider
from evoblue_video_mcp.asr.release_gate import (
    LITE_THRESHOLDS,
    STANDARD_THRESHOLDS,
    THRESHOLDS_VERSION,
    evaluate_gate,
)

_TIER_PROVIDER_IDS = {
    "standard": "sherpa-onnx-standard",
    "lite": "sherpa-onnx-lite",
}

_TIER_THRESHOLDS = {
    "standard": STANDARD_THRESHOLDS,
    "lite": LITE_THRESHOLDS,
}


def _default_models_dir() -> Path:
    from platformdirs import user_data_path

    return user_data_path("EvoBlue Video MCP", "EvoBlue") / "models"


def _build_provider(
    name: str, models_dir: Path | None
) -> ASRProvider:
    if name == "fake":
        return FakeASRProvider()
    provider_id = _TIER_PROVIDER_IDS.get(name)
    if provider_id is not None:
        from evoblue_video_mcp.asr.registration import register_available_asr_providers
        from evoblue_video_mcp.asr.registry import get_provider

        register_available_asr_providers(models_dir or _default_models_dir())
        provider = get_provider(provider_id)
        if provider is None:
            raise SystemExit(
                f"tier {name!r} is not ready under the models directory; "
                "install the model first (WebUI /models or scripts/fetch_model.py)"
            )
        return provider
    raise SystemExit(f"unknown provider: {name!r}; available: ['fake', 'standard', 'lite']")


def _audio_duration(path: str) -> float:
    with wave.open(path, "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


async def _score_corpus(
    provider: ASRProvider, corpus: BenchmarkCorpusManifest
) -> tuple[dict[str, object], dict[str, float]]:
    results: list[dict[str, object]] = []
    peak_before = current_peak_rss().peak_rss_bytes
    peak_after = peak_before
    for sample in corpus.samples:
        duration = _audio_duration(sample.audio_path) if sample.audio_path else 0.0
        started = time.perf_counter()
        hypothesis = await provider.transcribe(
            ASRRequest(audio_path=sample.audio_path or "", language=sample.language)
        )
        elapsed = time.perf_counter() - started
        text = " ".join(segment.text for segment in hypothesis.segments)
        reference = normalize_for_scoring(sample.reference_text)
        normalized = normalize_for_scoring(text)
        empty_reference = sample.reference_text == ""
        entry: dict[str, Any] = {
            "sample_id": sample.id,
            "language": sample.language,
            "cer": round(character_error_rate(reference, normalized), 6),
            "wer": round(
                word_error_rate(normalize_words(sample.reference_text), normalize_words(text)),
                6,
            ),
            "provider_id": hypothesis.provider_id,
            "model_id": hypothesis.model_id,
            "audio_seconds": round(duration, 3),
            "transcribe_seconds": round(elapsed, 3),
            "rtf": round(elapsed / duration, 4) if duration > 0 else None,
            "empty_reference": empty_reference,
            "hallucinated": empty_reference and bool(normalized),
            "entity_count": len(sample.entities),
            "entity_recall": round(entity_recall(sample.entities, text), 4),
        }
        results.append(entry)
        peak = current_peak_rss().peak_rss_bytes
        if peak is not None and (peak_after is None or peak > peak_after):
            peak_after = peak

    memory: dict[str, float] = {}
    if peak_before is not None and peak_after is not None:
        memory["peak_rss_bytes"] = float(peak_after)
        memory["peak_rss_delta_bytes"] = float(peak_after - peak_before)

    report: dict[str, object] = {
        "corpus": corpus.name,
        "provider": provider.provider_id,
        "results": results,
    }
    return report, memory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evoblue-video-mcp.asr.benchmark")
    parser.add_argument("corpus", help="path to a benchmark corpus manifest JSON")
    parser.add_argument(
        "--provider", default="fake", help="ASR provider: fake | standard | lite"
    )
    parser.add_argument(
        "--models-dir", default=None, help="model install root (real tiers)"
    )
    parser.add_argument("--out", default=None, help="write JSON report to this path")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="evaluate the report against the frozen release-gate thresholds",
    )
    args = parser.parse_args(argv)

    corpus = BenchmarkCorpusManifest.model_validate_json(
        Path(args.corpus).read_text(encoding="utf-8")
    )
    provider = _build_provider(args.provider, Path(args.models_dir) if args.models_dir else None)
    report, memory = asyncio.run(_score_corpus(provider, corpus))
    report["benchmark_meta"] = {
        "thresholds_version": THRESHOLDS_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": _cpu_count(),
        **memory,
    }

    if args.gate:
        thresholds = _TIER_THRESHOLDS[args.provider]
        verdict = evaluate_gate(report, thresholds)
        report["gate"] = {
            "tier": args.provider,
            "thresholds_version": thresholds.version,
            "passed": verdict.passed,
            "items": [
                {"name": i.name, "passed": i.passed, "detail": i.detail}
                for i in verdict.items
            ],
        }

    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    if args.gate:
        gate = report.get("gate", {})
        assert isinstance(gate, dict)
        return 0 if gate.get("passed") is True else 1
    return 0


def _cpu_count() -> int:
    import os

    return os.cpu_count() or 0


if __name__ == "__main__":
    raise SystemExit(main())
