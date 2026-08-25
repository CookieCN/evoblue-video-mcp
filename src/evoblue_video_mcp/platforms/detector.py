"""Resolve a video URL to a platform and video id."""

from urllib.parse import ParseResult, parse_qs, urlparse

from evoblue_video_mcp.platforms.models import Platform, VideoRef

INVALID_URL = "INVALID_URL"
UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"


class PlatformError(Exception):
    """Raised when a URL cannot be resolved to a supported video."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
_BILIBILI_HOSTS = frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com"})


def canonical_url(platform: Platform, video_id: str) -> str:
    """Return the canonical URL for a resolved video."""
    if platform is Platform.YOUTUBE:
        return f"https://www.youtube.com/watch?v={video_id}"
    if platform is Platform.BILIBILI:
        return f"https://www.bilibili.com/video/{video_id}"
    raise PlatformError(UNSUPPORTED_PLATFORM, f"no canonical URL for {platform}")


def detect_video(url: str) -> VideoRef:
    """Resolve ``url`` to a ``VideoRef`` with a canonical URL, or raise ``PlatformError``."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise PlatformError(INVALID_URL, f"invalid URL: {exc}") from exc

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise PlatformError(INVALID_URL, "URL must be http(s)")

    host = parsed.hostname.lower()
    if host in _YOUTUBE_HOSTS:
        video_id = _youtube_video_id(parsed)
        return VideoRef(Platform.YOUTUBE, video_id, canonical_url(Platform.YOUTUBE, video_id))
    if host in _BILIBILI_HOSTS:
        video_id = _bilibili_video_id(parsed)
        return VideoRef(Platform.BILIBILI, video_id, canonical_url(Platform.BILIBILI, video_id))

    raise PlatformError(UNSUPPORTED_PLATFORM, f"unsupported platform host: {host}")


def _youtube_video_id(parsed: ParseResult) -> str:
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.hostname == "youtu.be" and parts:
        return parts[0]
    if parts and parts[0] == "shorts" and len(parts) >= 2:
        return parts[1]
    query = parse_qs(parsed.query)
    values = query.get("v")
    if values and values[0]:
        return values[0]
    raise PlatformError(INVALID_URL, "YouTube URL is missing a video id")


def _bilibili_video_id(parsed: ParseResult) -> str:
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "video":
        return parts[1]
    raise PlatformError(INVALID_URL, "Bilibili URL is missing a video id")
