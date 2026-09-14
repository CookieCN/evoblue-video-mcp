"""System credential-store boundary for provider API keys."""

import uuid
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
    """LEGACY stable lookup key ``llm:{provider}`` for rows saved before
    versioned references existed. Reads still accept it; every new WRITE
    must use :func:`new_llm_credential_reference` instead."""
    normalized = provider.strip().lower() or "openai-compatible"
    return f"llm:{normalized}"


def new_llm_credential_reference(provider: str) -> str:
    """A FRESH, never-reused slot name for one provider credential write.

    R5 (review round 3): the keyring is a name-addressed vault, so writing
    the slot the database row still references lets any failure between the
    write and the SQLite commit (commit error, or a write that timed out and
    lands late) silently retarget the OLD endpoint's binding at the NEW key.
    Writes instead target their own unique slot: the database commit that
    adopts the slot is the switching point, and an unadopted write only
    leaves an unreferenced slot (cleaned up best-effort afterwards).
    """
    normalized = provider.strip().lower() or "openai-compatible"
    return f"llm:{normalized}:{uuid.uuid4().hex[:12]}"
