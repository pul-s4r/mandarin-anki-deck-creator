"""Provider registry: register once, resolve by name from CLI and future web API."""

from __future__ import annotations

from collections.abc import Callable

from anki_deck_generator.errors import IntegrationError
from anki_deck_generator.integrations.base import IntegrationProvider

_PROVIDERS: dict[str, type[IntegrationProvider]] = {}


def register_provider(name: str) -> Callable[[type[IntegrationProvider]], type[IntegrationProvider]]:
    """Decorator to register a provider class under a stable CLI/API name."""

    def _wrap(cls: type[IntegrationProvider]) -> type[IntegrationProvider]:
        _PROVIDERS[name] = cls
        return cls

    return _wrap


def get_provider(name: str) -> IntegrationProvider:
    """Return a new instance of the named provider, or raise IntegrationError."""
    try:
        cls = _PROVIDERS[name]
    except KeyError as exc:
        raise IntegrationError(f"unknown integration provider: {name!r}") from exc
    return cls()


def available_providers() -> list[str]:
    """Sorted list of registered provider names."""
    return sorted(_PROVIDERS.keys())
