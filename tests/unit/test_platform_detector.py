"""URL detection resolves platforms and video ids without any network access."""

import pytest

from evoblue_video_mcp.platforms.detector import (
    INVALID_URL,
    UNSUPPORTED_PLATFORM,
    PlatformError,
    detect_video,
)
from evoblue_video_mcp.platforms.models import Platform


def test_youtube_watch_url() -> None:
    ref = detect_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert ref.platform is Platform.YOUTUBE
    assert ref.video_id == "dQw4w9WgXcQ"


def test_youtube_share_url() -> None:
    ref = detect_video("https://youtu.be/dQw4w9WgXcQ")
    assert ref.platform is Platform.YOUTUBE
    assert ref.video_id == "dQw4w9WgXcQ"


def test_youtube_shorts_url() -> None:
    ref = detect_video("https://www.youtube.com/shorts/abc123XYZ")
    assert ref.platform is Platform.YOUTUBE
    assert ref.video_id == "abc123XYZ"


def test_youtube_mobile_url_with_query() -> None:
    ref = detect_video("https://m.youtube.com/watch?v=abc123XYZ&t=30")
    assert ref.platform is Platform.YOUTUBE
    assert ref.video_id == "abc123XYZ"


def test_bilibili_video_url() -> None:
    ref = detect_video("https://www.bilibili.com/video/BV1xx411c7mD")
    assert ref.platform is Platform.BILIBILI
    assert ref.video_id == "BV1xx411c7mD"


def test_unsupported_platform() -> None:
    with pytest.raises(PlatformError) as exc:
        detect_video("https://vimeo.com/12345")
    assert exc.value.error_code == UNSUPPORTED_PLATFORM


def test_non_http_url_is_rejected() -> None:
    with pytest.raises(PlatformError) as exc:
        detect_video("file:///tmp/video")
    assert exc.value.error_code == INVALID_URL


def test_youtube_missing_video_id_is_rejected() -> None:
    with pytest.raises(PlatformError) as exc:
        detect_video("https://www.youtube.com/watch")
    assert exc.value.error_code == INVALID_URL


def test_youtube_urls_normalize_to_canonical() -> None:
    canonical = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert detect_video("https://youtu.be/dQw4w9WgXcQ").url == canonical
    assert detect_video("https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=30").url == canonical
    assert detect_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ").url == canonical


def test_bilibili_url_normalizes_to_canonical() -> None:
    assert (
        detect_video("https://www.bilibili.com/video/BV1xx411c7mD").url
        == "https://www.bilibili.com/video/BV1xx411c7mD"
    )
