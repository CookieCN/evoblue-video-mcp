"""Qualify candidate mainland-China download sources (dev-only, read-only).

ASR-2 step 3 chose "machinery + evidence, no mirror yet": this script measures
candidate China mirrors without approving or shipping any of them. For each
candidate URL it checks reachability, served size (Content-Length) and Range
support (a 206 to a small ``bytes=0-N`` probe), which is the property the
resumable downloader needs. It deliberately does not download a full artifact.

Run:  uv run python scripts/qualify_china_sources.py

The report is evidence for docs/ASR_CHINA_SOURCE_QUALIFICATION.md. It never
changes the manifest/catalog or writes any approval.
"""

import asyncio
import sys

import httpx

# The int8 artifacts are published as single ``.tar.bz2`` archives on GitHub
# Releases; huggingface.co mirrors (and their hf-mirror.com China mirror) serve
# the same weights as loose files under a different repo layout, so their bytes
# and SHA-256 differ from the pinned archive. That mismatch is exactly why they
# cannot drop into the frozen single-archive manifest contract without review.
CANDIDATES = [
    {
        "name": "SenseVoice int8 model (hf-mirror loose file)",
        "url": (
            "https://hf-mirror.com/csukuangfj/"
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/model.int8.onnx"
        ),
    },
    {
        "name": "SenseVoice tokens (hf-mirror loose file)",
        "url": (
            "https://hf-mirror.com/csukuangfj/"
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/tokens.txt"
        ),
    },
]


async def _probe(client: httpx.AsyncClient, url: str) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        head = await client.head(url, follow_redirects=True)
        result["status"] = str(head.status_code)
        result["content_length"] = head.headers.get("content-length", "?")
        result["accept_ranges"] = head.headers.get("accept-ranges", "?")
    except httpx.HTTPError as exc:
        result["status"] = f"error: {type(exc).__name__}"
        return result

    # A small Range probe confirms the mirror can resume a partial download.
    try:
        probe = await client.get(url, headers={"Range": "bytes=0-1023"})
        result["range_status"] = str(probe.status_code)
        result["range_content_range"] = probe.headers.get("content-range", "?")
    except httpx.HTTPError as exc:
        result["range_status"] = f"error: {type(exc).__name__}"
    return result


async def main() -> int:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for candidate in CANDIDATES:
            print(f"\n## {candidate['name']}\n    {candidate['url']}")
            result = await _probe(client, candidate["url"])
            fields = (
                "status",
                "content_length",
                "accept_ranges",
                "range_status",
                "range_content_range",
            )
            for key in fields:
                if key in result:
                    print(f"    {key}: {result[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
