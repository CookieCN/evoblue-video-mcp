"""Synthesize chunk summaries into structured report fields via the LLM."""

import json
import re
from typing import Any

from evoblue_video_mcp.llm.base import LLMProvider


class SynthesisError(RuntimeError):
    """Raised when the LLM's synthesis output cannot be parsed."""


_SYNTHESIS_PROMPT = (
    "You are producing the sections of a structured video analysis report. "
    "Given the chunk summaries below, return a JSON object with these keys:\n"
    '- "core_summary": a concise summary of the whole video\n'
    '- "key_takeaways": a list of 3-5 short key takeaways\n'
    '- "timeline_outline": a markdown outline of the video structure\n'
    '- "content_analysis": analysis of the argument and structure\n'
    "Return only the JSON object, no commentary.\n\n"
    "Chunk summaries:\n{summaries}"
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def parse_synthesis(raw: str) -> dict[str, Any]:
    """Parse the LLM's JSON output, tolerating a surrounding markdown fence."""
    stripped = _FENCE_RE.sub(r"\1", raw.strip()).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise SynthesisError(f"invalid synthesis output: {exc}") from exc
    if not isinstance(data, dict):
        raise SynthesisError("synthesis output is not a JSON object")
    return data


def _synthesis_prompt(summaries: list[str]) -> str:
    return _SYNTHESIS_PROMPT.format(summaries="\n".join(f"- {s}" for s in summaries))


async def synthesize(llm: LLMProvider, summaries: list[str]) -> dict[str, Any]:
    """Ask the LLM to synthesize summaries into structured report sections."""
    raw = await llm.complete(_synthesis_prompt(summaries))
    return parse_synthesis(raw)
