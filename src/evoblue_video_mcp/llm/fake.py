"""Deterministic in-memory LLM provider for tests and development."""


class FakeLLMProvider:
    """Returns a fixed completion; used where no real model is configured."""

    def __init__(self, response: str = "fake completion") -> None:
        self._response = response

    async def complete(self, prompt: str) -> str:
        return self._response
