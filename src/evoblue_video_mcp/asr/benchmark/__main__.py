"""Reproducible ASR benchmark scoring command.

Usage:
    python -m evoblue_video_mcp.asr.benchmark CORPUS_JSON [--provider fake] [--out report.json]
"""

import argparse
import asyncio
import json
from pathlib import Path

from evoblue_video_mcp.asr.base import ASRProvider, ASRRequest
from evoblue_video_mcp.asr.benchmark.manifest import BenchmarkCorpusManifest
from evoblue_video_mcp.asr.benchmark.metrics import character_error_rate, word_error_rate
from evoblue_video_mcp.asr.fake import FakeASRProvider


def _build_provider(name: str) -> ASRProvider:
    if name == "fake":
        return FakeASRProvider()
    raise SystemExit(f"unknown provider: {name!r}; available: ['fake']")


async def _score_corpus(
    provider: ASRProvider, corpus: BenchmarkCorpusManifest
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for sample in corpus.samples:
        hypothesis = await provider.transcribe(
            ASRRequest(audio_path=sample.audio_path or "", language=sample.language)
        )
        text = " ".join(segment.text for segment in hypothesis.segments)
        results.append(
            {
                "sample_id": sample.id,
                "language": sample.language,
                "cer": round(character_error_rate(sample.reference_text, text), 6),
                "wer": round(word_error_rate(sample.reference_text, text), 6),
                "provider_id": hypothesis.provider_id,
                "model_id": hypothesis.model_id,
            }
        )
    return {"corpus": corpus.name, "provider": provider.provider_id, "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evoblue-video-mcp.asr.benchmark")
    parser.add_argument("corpus", help="path to a benchmark corpus manifest JSON")
    parser.add_argument("--provider", default="fake", help="ASR provider to score with")
    parser.add_argument("--out", default=None, help="write JSON report to this path")
    args = parser.parse_args(argv)

    corpus = BenchmarkCorpusManifest.model_validate_json(
        Path(args.corpus).read_text(encoding="utf-8")
    )
    provider = _build_provider(args.provider)
    report = asyncio.run(_score_corpus(provider, corpus))

    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
