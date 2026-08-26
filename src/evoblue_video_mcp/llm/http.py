"""OpenAI-compatible HTTP LLM provider with auth, rate-limit, and timeout mapping."""

from typing import Any

import httpx

from evoblue_video_mcp.llm.base import (
    LLM_AUTH_FAILED,
    LLM_NOT_CONFIGURED,
    LLM_RATE_LIMITED,
    LLM_REQUEST_FAILED,
    LLM_RESPONSE_INVALID,
    LLMError,
)


class HttpLLMProvider:
    """Calls an OpenAI-compatible ``/chat/completions`` endpoint.

    The API key is only used in request headers and never logged or persisted.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._http_client = http_client

    async def complete(self, prompt: str) -> str:
        if not self._api_key or not self._base_url or not self._model:
            raise LLMError(LLM_NOT_CONFIGURED, "LLM provider is not fully configured")

        url = f"{self._base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        payload = {"model": self._model, "messages": [{"role": "user", "content": prompt}]}

        try:
            if self._http_client is not None:
                resp = await self._http_client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMError(LLM_RATE_LIMITED, "LLM request timed out", retryable=True) from exc
        except httpx.RequestError as exc:
            raise LLMError(LLM_RATE_LIMITED, "LLM network request failed", retryable=True) from exc

        if resp.status_code in (401, 403):
            raise LLMError(LLM_AUTH_FAILED, f"LLM auth failed: HTTP {resp.status_code}")
        if resp.status_code == 429 or resp.status_code >= 500:
            raise LLMError(
                LLM_RATE_LIMITED, f"LLM request failed: HTTP {resp.status_code}", retryable=True
            )
        if resp.status_code >= 400:
            raise LLMError(
                LLM_REQUEST_FAILED,
                f"LLM request rejected: HTTP {resp.status_code}",
                retryable=False,
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(
                LLM_RESPONSE_INVALID, "LLM returned invalid JSON", retryable=False
            ) from exc
        return _extract_content(data)


def _extract_content(data: Any) -> str:
    if not isinstance(data, dict):
        raise LLMError(LLM_RESPONSE_INVALID, "LLM response is not an object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LLMError(LLM_RESPONSE_INVALID, "LLM response has no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise LLMError(LLM_RESPONSE_INVALID, "LLM response has no message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMError(LLM_RESPONSE_INVALID, "LLM response content is empty")
    return content
