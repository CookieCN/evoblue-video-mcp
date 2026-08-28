"""ASR provider registry: register, lookup, list."""

from evoblue_video_mcp.asr.fake import FakeASRProvider
from evoblue_video_mcp.asr.registry import clear, get_provider, list_providers, register_provider


def test_register_and_lookup() -> None:
    clear()
    register_provider(FakeASRProvider())
    provider = get_provider("fake")
    assert provider is not None
    assert provider.provider_id == "fake"


def test_unknown_provider_returns_none() -> None:
    clear()
    assert get_provider("nope") is None


def test_list_providers_is_sorted() -> None:
    clear()
    register_provider(FakeASRProvider())
    assert list_providers() == ["fake"]
