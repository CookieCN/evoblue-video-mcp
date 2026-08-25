"""yt-dlp based adapter for YouTube and Bilibili metadata and subtitles."""

import asyncio
import urllib.error
from typing import Any, cast

import httpx
import yt_dlp  # type: ignore[import-untyped]
import yt_dlp.utils  # type: ignore[import-untyped]

from evoblue_video_mcp.platforms.base import (
    METADATA_FETCH_FAILED,
    SUBTITLE_UNAVAILABLE,
    AdapterError,
)
from evoblue_video_mcp.platforms.models import (
    Platform,
    Transcript,
    TranscriptSegment,
    VideoMetadata,
    VideoRef,
)
from evoblue_video_mcp.transcript.parser import parse_srt, parse_vtt

_PREFERRED_LANGS = ["zh-Hans", "zh-CN", "zh", "en"]
# yt-dlp exposes many subtitle formats; only these two are parsed today.
_SUPPORTED_SUBTITLE_EXTS = ("vtt", "srt")
# HTTP statuses that are worth retrying (rate-limit, server hiccup, gateways).
_RETRYABLE_STATUSES = frozenset({403, 429, 500, 502, 503, 504})


class YtDlpAdapter:
    """Fetches metadata and subtitles via yt-dlp for YouTube and Bilibili."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    def supports(self, ref: VideoRef) -> bool:
        return ref.platform in (Platform.YOUTUBE, Platform.BILIBILI)

    async def fetch_metadata(self, ref: VideoRef) -> VideoMetadata:
        try:
            info = await asyncio.to_thread(self._extract_info, ref)
        except yt_dlp.utils.DownloadError as exc:
            raise AdapterError(
                METADATA_FETCH_FAILED, str(exc), retryable=_is_retryable_download_error(exc)
            ) from exc

        title = str(info.get("title") or "")
        if not title:
            raise AdapterError(METADATA_FETCH_FAILED, "yt-dlp returned no title")
        return VideoMetadata(
            video_id=ref.video_id,
            platform=ref.platform,
            title=title,
            author=str(info.get("uploader") or ""),
            duration=_as_optional_float(info.get("duration")),
            published_at=_format_upload_date(info.get("upload_date")),
        )

    async def fetch_transcript(self, ref: VideoRef) -> Transcript:
        try:
            info = await asyncio.to_thread(self._extract_info, ref)
        except yt_dlp.utils.DownloadError as exc:
            raise AdapterError(
                SUBTITLE_UNAVAILABLE, str(exc), retryable=_is_retryable_download_error(exc)
            ) from exc

        picked = self._pick_subtitle_url(info)
        if picked is None:
            raise AdapterError(
                SUBTITLE_UNAVAILABLE, "no supported subtitles available", retryable=False
            )

        url, ext, lang = picked
        raw = await self._download(url)
        try:
            segments = self._parse(raw, ext)
        except ValueError as exc:
            raise AdapterError(
                SUBTITLE_UNAVAILABLE, f"subtitle format invalid: {exc}", retryable=False
            ) from exc
        if not segments:
            raise AdapterError(SUBTITLE_UNAVAILABLE, "subtitle text was empty", retryable=False)
        return Transcript(
            segments=segments, language=lang, source=f"{ref.platform.value}-subtitle"
        )

    def _extract_info(self, ref: VideoRef) -> dict[str, Any]:
        opts = {
            "quiet": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return cast(dict[str, Any], ydl.extract_info(ref.url, download=False))

    async def _download(self, url: str) -> str:
        try:
            if self._http_client is not None:
                resp = await self._http_client.get(url)
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code in _RETRYABLE_STATUSES
            raise AdapterError(
                SUBTITLE_UNAVAILABLE,
                f"subtitle download failed: HTTP {exc.response.status_code}",
                retryable=retryable,
            ) from exc
        except httpx.TimeoutException as exc:
            raise AdapterError(
                SUBTITLE_UNAVAILABLE, "subtitle download timed out", retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise AdapterError(
                SUBTITLE_UNAVAILABLE, f"subtitle download failed: {exc}", retryable=True
            ) from exc

    def _pick_subtitle_url(self, info: dict[str, Any]) -> tuple[str, str, str] | None:
        for source_key in ("subtitles", "automatic_captions"):
            subs = info.get(source_key) or {}
            for lang in _PREFERRED_LANGS:
                picked = _pick_from(subs.get(lang), lang)
                if picked is not None:
                    return picked
            for lang, entries in subs.items():
                picked = _pick_from(entries, lang)
                if picked is not None:
                    return picked
        return None

    def _parse(self, raw: str, ext: str) -> list[TranscriptSegment]:
        if ext == "srt":
            return parse_srt(raw)
        return parse_vtt(raw)


def _pick_from(entries: Any, lang: str) -> tuple[str, str, str] | None:
    """Pick the first supported subtitle format (vtt/srt) from a language's entries."""
    if not entries:
        return None
    for entry in entries:
        url = entry.get("url")
        ext = entry.get("ext")
        if url and ext in _SUPPORTED_SUBTITLE_EXTS:
            return url, ext, lang
    return None


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_upload_date(value: Any) -> str | None:
    text = str(value or "")
    if len(text) != 8 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _is_retryable_download_error(exc: Any) -> bool:
    """Return True when a yt-dlp DownloadError wraps a transient network failure.

    Permanent errors (video removed, private, region-locked, login required) and
    anything we cannot classify are treated as non-retryable by default.
    """
    cause = getattr(exc, "exc_info", None)
    # yt-dlp stores exc_info as a sys.exc_info() tuple: (type, value, traceback).
    exc_value = cause[1] if isinstance(cause, tuple) and len(cause) >= 2 else cause
    if isinstance(exc_value, OSError):
        return True
    if isinstance(exc_value, urllib.error.URLError):
        return isinstance(exc_value.reason, OSError)
    return False
