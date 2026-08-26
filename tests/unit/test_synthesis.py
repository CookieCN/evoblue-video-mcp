"""Synthesis parses the LLM's structured JSON output."""

import json

import pytest

from evoblue_video_mcp.llm.fake import FakeLLMProvider
from evoblue_video_mcp.reports.synthesis import SynthesisError, parse_synthesis, synthesize


def test_parse_synthesis_plain_json() -> None:
    data = parse_synthesis(
        json.dumps(
            {
                "core_summary": "summary",
                "key_takeaways": ["a", "b"],
                "timeline_outline": "outline",
                "content_analysis": "analysis",
            }
        )
    )
    assert data["core_summary"] == "summary"
    assert data["key_takeaways"] == ["a", "b"]


def test_parse_synthesis_strips_code_fence() -> None:
    data = parse_synthesis('```json\n{"core_summary": "s"}\n```')
    assert data["core_summary"] == "s"


def test_parse_synthesis_rejects_invalid_json() -> None:
    with pytest.raises(SynthesisError):
        parse_synthesis("not json")


async def test_synthesize() -> None:
    llm = FakeLLMProvider(
        json.dumps(
            {
                "core_summary": "s",
                "key_takeaways": ["a"],
                "timeline_outline": "",
                "content_analysis": "",
            }
        )
    )
    data = await synthesize(llm, ["chunk 1"])
    assert data["core_summary"] == "s"
