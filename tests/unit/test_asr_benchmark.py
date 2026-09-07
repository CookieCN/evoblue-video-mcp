"""Benchmark runner: reproducible JSON report with the fake provider.

Reproducibility is asserted on the STABLE report exactly (per ASR-4 review):
peak-RSS numbers are allocator/OS artifacts that differ between runs by
design, so they are checked for presence and sanity — never for equality
(experience #24: 逐次完全相等的内存断言没有产品意义).
"""

import copy
import json
import math
from pathlib import Path

from evoblue_video_mcp.asr.benchmark.__main__ import main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "asr_benchmark"

#: Volatile memory measurements — present in every report, never compared.
_MEMORY_FIELDS = ("peak_rss_bytes", "peak_rss_delta_bytes")


def _strip_memory(report: dict) -> dict:
    stable = copy.deepcopy(report)
    memory = stable.get("benchmark_meta", {})
    for field in _MEMORY_FIELDS:
        memory.pop(field, None)
    return stable


def test_benchmark_report_is_reproducible(tmp_path) -> None:
    corpus = FIXTURES / "corpus.json"
    out = tmp_path / "report.json"

    assert main([str(corpus), "--provider", "fake", "--out", str(out)]) == 0
    first = json.loads(out.read_text(encoding="utf-8"))
    assert first["corpus"] == "evoblue-asr-smoke"
    assert first["provider"] == "fake"
    assert len(first["results"]) == 2

    assert main([str(corpus), "--provider", "fake", "--out", str(out)]) == 0
    second = json.loads(out.read_text(encoding="utf-8"))
    assert _strip_memory(second) == _strip_memory(first)


def test_benchmark_report_carries_sane_memory_metrics(tmp_path) -> None:
    corpus = FIXTURES / "corpus.json"
    out = tmp_path / "report.json"

    assert main([str(corpus), "--provider", "fake", "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    meta = report["benchmark_meta"]
    for field in _MEMORY_FIELDS:
        assert field in meta, f"memory metric {field} missing from report"
        assert isinstance(meta[field], (int, float))
        assert math.isfinite(meta[field])
        assert meta[field] >= 0
