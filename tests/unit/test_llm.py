"""LLM provider contract: fake provider and stable error codes."""

from evoblue_video_mcp.llm.base import (
    LLM_AUTH_FAILED,
    LLM_NOT_CONFIGURED,
    LLM_RATE_LIMITED,
    LLMError,
)
from evoblue_video_mcp.llm.fake import FakeLLMProvider


async def test_fake_provider_returns_fixed_response() -> None:
    provider = FakeLLMProvider("summary")
    assert await provider.complete("prompt") == "summary"


def test_llm_error_codes_and_retryability() -> None:
    auth = LLMError(LLM_AUTH_FAILED, "bad key")
    assert auth.error_code == LLM_AUTH_FAILED
    assert auth.retryable is False

    rate = LLMError(LLM_RATE_LIMITED, "429", retryable=True)
    assert rate.error_code == LLM_RATE_LIMITED
    assert rate.retryable is True

    not_configured = LLMError(LLM_NOT_CONFIGURED, "no provider")
    assert not_configured.error_code == LLM_NOT_CONFIGURED
    assert not_configured.retryable is False
