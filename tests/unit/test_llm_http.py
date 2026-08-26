"""HTTP LLM provider maps auth, rate-limit, and timeout failures to stable codes."""

import httpx
import pytest

from evoblue_video_mcp.llm.base import (
    LLM_AUTH_FAILED,
    LLM_NOT_CONFIGURED,
    LLM_RATE_LIMITED,
    LLM_REQUEST_FAILED,
    LLM_RESPONSE_INVALID,
    LLMError,
)
from evoblue_video_mcp.llm.http import HttpLLMProvider

_BASE = "https://api.openai.com/v1"


def _client(status_code: int = 200, body: dict | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body or {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _provider(client: httpx.AsyncClient, api_key: str = "key") -> HttpLLMProvider:
    return HttpLLMProvider(base_url=_BASE, api_key=api_key, model="m", http_client=client)


async def test_complete_returns_content() -> None:
    client = _client(200, {"choices": [{"message": {"content": "hello"}}]})
    assert await _provider(client).complete("prompt") == "hello"


async def test_auth_failure_is_not_retryable() -> None:
    client = _client(401, {})
    with pytest.raises(LLMError) as exc:
        await _provider(client).complete("prompt")
    assert exc.value.error_code == LLM_AUTH_FAILED
    assert exc.value.retryable is False


async def test_rate_limit_is_retryable() -> None:
    client = _client(429, {})
    with pytest.raises(LLMError) as exc:
        await _provider(client).complete("prompt")
    assert exc.value.error_code == LLM_RATE_LIMITED
    assert exc.value.retryable is True


async def test_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as exc:
        await _provider(client).complete("prompt")
    assert exc.value.error_code == LLM_RATE_LIMITED
    assert exc.value.retryable is True


async def test_missing_api_key_is_not_configured() -> None:
    provider = HttpLLMProvider(base_url=_BASE, api_key="", model="m")
    with pytest.raises(LLMError) as exc:
        await provider.complete("prompt")
    assert exc.value.error_code == LLM_NOT_CONFIGURED


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        ["not-an-object"],
    ],
)
async def test_malformed_success_response_has_stable_error(body) -> None:
    client = _client(200, body)
    with pytest.raises(LLMError) as exc:
        await _provider(client).complete("prompt")
    assert exc.value.error_code == LLM_RESPONSE_INVALID
    assert exc.value.retryable is False


async def test_non_json_success_response_has_stable_error() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not-json"))
    )
    with pytest.raises(LLMError) as exc:
        await _provider(client).complete("prompt")
    assert exc.value.error_code == LLM_RESPONSE_INVALID


async def test_non_auth_client_error_is_not_misreported_as_auth() -> None:
    client = _client(400, {})
    with pytest.raises(LLMError) as exc:
        await _provider(client).complete("prompt")
    assert exc.value.error_code == LLM_REQUEST_FAILED
