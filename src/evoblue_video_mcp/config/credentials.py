"""System credential-store boundary for provider API keys."""

from contextlib import suppress
from typing import Protocol

import keyring

SERVICE_NAME = "evoblue-video-mcp"


class CredentialStore(Protocol):
    """Minimal credential operations needed by the WebUI and runtime."""

    def get_secret(self, reference: str) -> str | None: ...

    def set_secret(self, reference: str, secret: str) -> None: ...

    def delete_secret(self, reference: str) -> None: ...


class KeyringCredentialStore:
    """Store secrets in the operating system keyring, never SQLite."""

    def get_secret(self, reference: str) -> str | None:
        return keyring.get_password(SERVICE_NAME, reference)

    def set_secret(self, reference: str, secret: str) -> None:
        keyring.set_password(SERVICE_NAME, reference, secret)

    def delete_secret(self, reference: str) -> None:
        with suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(SERVICE_NAME, reference)


def llm_credential_reference(provider: str) -> str:
    """Return a stable, non-secret lookup key for one provider credential."""
    normalized = provider.strip().lower() or "openai-compatible"
    return f"llm:{normalized}"
