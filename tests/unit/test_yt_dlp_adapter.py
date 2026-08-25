"""yt-dlp adapter is tested against a fake extractor and mock HTTP transport (no network)."""

import httpx
import pytest

from evoblue_video_mcp.platforms.base import SUBTITLE_UNAVAILABLE, AdapterError
from evoblue_video_mcp.platforms.models import Platform, VideoRef
from evoblue_video_mcp.platforms.yt_dlp_adapter import YtDlpAdapter

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


async def test_fetch_transcript_unavailable(patch_ytdlp) -> None:
    patch_ytdlp({"title": "No subs", "subtitles": {}, "automatic_captions": {}})
    adapter = YtDlpAdapter()
    ref = VideoRef(Platform.BILIBILI, "BV1xx", "https://www.bilibili.com/video/BV1xx")

    with pytest.raises(AdapterError) as exc:
        await adapter.fetch_transcript(ref)
    assert exc.value.error_code == SUBTITLE_UNAVAILABLE
