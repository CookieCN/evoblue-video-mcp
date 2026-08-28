"""Provider registry: the only way the core obtains ASR implementations.

Core layers (assembly, handlers) resolve providers through this module and must
never import concrete engines under ``asr/providers/`` directly. Real engines
register themselves at bootstrap time when their optional dependency is present.
"""

from evoblue_video_mcp.asr.base import ASRProvider

_REGISTRY: dict[str, ASRProvider] = {}


def register_provider(provider: ASRProvider) -> None:
    """Register a provider under its ``provider_id``, replacing any prior entry."""
    _REGISTRY[provider.provider_id] = provider


def unregister_provider(provider_id: str) -> None:
    """Remove a registration if present; a no-op when the id is not registered."""
    _REGISTRY.pop(provider_id, None)


def get_provider(provider_id: str) -> ASRProvider | None:
    """Return a registered provider, or ``None`` if not installed."""
    return _REGISTRY.get(provider_id)


def list_providers() -> list[str]:
    """Return the sorted list of registered provider ids."""
    return sorted(_REGISTRY)


def clear() -> None:
    """Remove all registrations (tests only)."""
    _REGISTRY.clear()
