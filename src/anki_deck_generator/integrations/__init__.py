"""External source integrations (optional subsystems, lazy-loaded from CLI)."""

from anki_deck_generator.integrations.base import ImportedDocument, ImportResult, IntegrationProvider
from anki_deck_generator.integrations.registry import available_providers, get_provider, register_provider

# echo is not imported here: ``cli`` loads it via importlib when the ``import`` subcommand runs
# so the toy provider registers only for that code path.

__all__ = [
    "ImportResult",
    "ImportedDocument",
    "IntegrationProvider",
    "available_providers",
    "get_provider",
    "register_provider",
]
