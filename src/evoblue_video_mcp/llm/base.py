"""LLM provider contract and stable error codes."""

from typing import Protocol

LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
LLM_AUTH_FAILED = "LLM_AUTH_FAILED"
LLM_RATE_LIMITED = "LLM_RATE_LIMITED"


class LLMError(Exception):
    """Raised when an LLM provider call fails."""

    def __init__(self, error_code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class LLMProvider(Protocol):
    """Completes a prompt; implementations wrap a real or fake model backend."""

    async def complete(self, prompt: str) -> str: ...
