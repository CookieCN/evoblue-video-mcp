"""yt-dlp adapter is tested against a fake extractor and mock HTTP transport (no network)."""

import urllib.error

import httpx
import pytest
import yt_dlp.utils

from evoblue_video_mcp.platforms.base import (
    METADATA_FETCH_FAILED,
    SUBTITLE_MISSING,
    SUBTITLE_UNAVAILABLE,
    AdapterError,
)
from evoblue_video_mcp.platforms.models import Platform, VideoRef
from evoblue_video_mcp.platforms.yt_dlp_adapter import (
    YtDlpAdapter,
    _is_retryable_download_error,
)

FAKE_INFO = {
    "title": "Test Video",
    "uploader": "Test Author",
    "duration": 120,
    "upload_date": "20240101",
    "subtitles": {
        "en": [{"url": "https://example.com/sub.vtt", "ext": "vtt"}],
    },
}

VTT_TEXT = (
    "WEBVTT\n\n"
    "00:00.000 --> 00:02.000\nHello world\n\n"
    "00:02.000 --> 00:04.000\nSecond line\n"
)


class _FakeYdl:
    def __init__(self, opts, info=FAKE_INFO):
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        return self._info


@pytest.fixture
def patch_ytdlp(monkeypatch):
    def _patch(info=FAKE_INFO):
        class _Ydl(_FakeYdl):
            def __init__(self, opts):
                super().__init__(opts, info)

        monkeypatch.setattr(
            "evoblue_video_mcp.platforms.yt_dlp_adapter.yt_dlp.YoutubeDL", _Ydl
        )

    return _patch


def _mock_client(text: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=text)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_metadata(patch_ytdlp) -> None:
    patch_ytdlp()
    adapter = YtDlpAdapter()
    ref = VideoRef(Platform.YOUTUBE, "abc", "https://youtu.be/abc")

    metadata = await adapter.fetch_metadata(ref)
    assert metadata.title == "Test Video"
    assert metadata.author == "Test Author"
    assert metadata.duration == 120.0
    assert metadata.published_at == "2024-01-01"


async def test_fetch_transcript(patch_ytdlp) -> None:
    patch_ytdlp()
    adapter = YtDlpAdapter(http_client=_mock_client(VTT_TEXT))
    ref = VideoRef(Platform.YOUTUBE, "abc", "https://youtu.be/abc")

    transcript = await adapter.fetch_transcript(ref)
    assert transcript.language == "en"
    assert len(transcript.segments) == 2
    assert transcript.segments[0].text == "Hello world"
    assert transcript.segments[1].end == 4.0


async def test_fetch_transcript_missing(patch_ytdlp) -> None:
    patch_ytdlp({"title": "No subs", "subtitles": {}, "automatic_captions": {}})
    adapter = YtDlpAdapter()
    ref = VideoRef(Platform.BILIBILI, "BV1xx", "https://www.bilibili.com/video/BV1xx")

    with pytest.raises(AdapterError) as exc:
        await adapter.fetch_transcript(ref)
    assert exc.value.error_code == SUBTITLE_MISSING


def _mock_status_client(status_code: int) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _mock_timeout_client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_prefers_vtt_over_json3(patch_ytdlp) -> None:
    info = {
        "title": "T",
        "subtitles": {
            "en": [
                {"url": "https://example.com/sub.json3", "ext": "json3"},
                {"url": "https://example.com/sub.vtt", "ext": "vtt"},
            ],
        },
    }
    patch_ytdlp(info)
    adapter = YtDlpAdapter(http_client=_mock_client(VTT_TEXT))
    ref = VideoRef(Platform.YOUTUBE, "abc", "https://youtu.be/abc")

    transcript = await adapter.fetch_transcript(ref)
    assert transcript.language == "en"
    assert len(transcript.segments) == 2


async def test_fetch_metadata_download_error(monkeypatch) -> None:
    class _RaisingYdl:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            exc = ConnectionError("refused")
            raise yt_dlp.utils.DownloadError("boom", exc_info=(type(exc), exc, None))

    monkeypatch.setattr(
        "evoblue_video_mcp.platforms.yt_dlp_adapter.yt_dlp.YoutubeDL", _RaisingYdl
    )
    adapter = YtDlpAdapter()
    ref = VideoRef(Platform.YOUTUBE, "abc", "https://youtu.be/abc")

    with pytest.raises(AdapterError) as exc:
        await adapter.fetch_metadata(ref)
    assert exc.value.error_code == METADATA_FETCH_FAILED
    assert exc.value.retryable is True


async def test_fetch_transcript_http_429_is_retryable(patch_ytdlp) -> None:
    patch_ytdlp()
    adapter = YtDlpAdapter(http_client=_mock_status_client(429))
    ref = VideoRef(Platform.YOUTUBE, "abc", "https://youtu.be/abc")

    with pytest.raises(AdapterError) as exc:
        await adapter.fetch_transcript(ref)
    assert exc.value.error_code == SUBTITLE_UNAVAILABLE
    assert exc.value.retryable is True


async def test_fetch_transcript_timeout_is_retryable(patch_ytdlp) -> None:
    patch_ytdlp()
    adapter = YtDlpAdapter(http_client=_mock_timeout_client())
    ref = VideoRef(Platform.YOUTUBE, "abc", "https://youtu.be/abc")

    with pytest.raises(AdapterError) as exc:
        await adapter.fetch_transcript(ref)
    assert exc.value.error_code == SUBTITLE_UNAVAILABLE
    assert exc.value.retryable is True


async def test_fetch_transcript_bad_format_is_not_retryable(patch_ytdlp) -> None:
    patch_ytdlp()
    adapter = YtDlpAdapter(http_client=_mock_client("WEBVTT\n\nnot-a-time --> 00:02.000\nhello\n"))
    ref = VideoRef(Platform.YOUTUBE, "abc", "https://youtu.be/abc")

    with pytest.raises(AdapterError) as exc:
        await adapter.fetch_transcript(ref)
    assert exc.value.error_code == SUBTITLE_MISSING
    assert exc.value.retryable is False


def _mock_connect_error_client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_transcript_connect_error_is_retryable(patch_ytdlp) -> None:
    patch_ytdlp()
    adapter = YtDlpAdapter(http_client=_mock_connect_error_client())
    ref = VideoRef(Platform.YOUTUBE, "abc", "https://youtu.be/abc")

    with pytest.raises(AdapterError) as exc:
        await adapter.fetch_transcript(ref)
    assert exc.value.error_code == SUBTITLE_UNAVAILABLE
    assert exc.value.retryable is True


async def test_permanent_download_error_not_retryable(monkeypatch) -> None:
    class _PermanentYdl:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            exc = yt_dlp.utils.ExtractorError("private video")
            raise yt_dlp.utils.DownloadError(
                "video unavailable", exc_info=(type(exc), exc, None)
            )

    monkeypatch.setattr(
        "evoblue_video_mcp.platforms.yt_dlp_adapter.yt_dlp.YoutubeDL", _PermanentYdl
    )
    adapter = YtDlpAdapter()
    ref = VideoRef(Platform.YOUTUBE, "abc", "https://youtu.be/abc")

    with pytest.raises(AdapterError) as exc:
        await adapter.fetch_metadata(ref)
    assert exc.value.error_code == METADATA_FETCH_FAILED
    assert exc.value.retryable is False


def _dl_error(exc: BaseException) -> yt_dlp.utils.DownloadError:
    return yt_dlp.utils.DownloadError("x", exc_info=(type(exc), exc, None))


def test_http_404_not_retryable() -> None:
    exc = urllib.error.HTTPError("https://x", 404, "Not Found", {}, None)
    assert _is_retryable_download_error(_dl_error(exc)) is False


def test_http_429_retryable() -> None:
    exc = urllib.error.HTTPError("https://x", 429, "Too Many Requests", {}, None)
    assert _is_retryable_download_error(_dl_error(exc)) is True


def test_urlerror_string_reason_not_retryable() -> None:
    exc = urllib.error.URLError("permanent reason")
    assert _is_retryable_download_error(_dl_error(exc)) is False


def test_connection_error_retryable() -> None:
    exc = ConnectionError("refused")
    assert _is_retryable_download_error(_dl_error(exc)) is True
