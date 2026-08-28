"""Benchmark runner: reproducible JSON report with the fake provider."""

import json
from pathlib import Path

from evoblue_video_mcp.asr.benchmark.__main__ import main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "asr_benchmark"


def test_benchmark_report_is_reproducible(tmp_path) -> None:
    corpus = FIXTURES / "corpus.json"
    out = tmp_path / "report.json"

    assert main([str(corpus), "--provider", "fake", "--out", str(out)]) == 0
    first = out.read_text(encoding="utf-8")
    report = json.loads(first)
    assert report["corpus"] == "evoblue-asr-smoke"
    assert report["provider"] == "fake"
    assert len(report["results"]) == 2

    assert main([str(corpus), "--provider", "fake", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == first
