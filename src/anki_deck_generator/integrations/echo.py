"""Minimal toy provider for tests and manual smoke checks of the import command."""

from __future__ import annotations

from anki_deck_generator.integrations.base import ImportedDocument, ImportResult, IntegrationProvider
from anki_deck_generator.integrations.registry import register_provider


@register_provider("echo")
class EchoProvider(IntegrationProvider):
    name = "echo"

    def authenticate(self, _credentials: dict) -> None:
        return None

    def list_sources(self, **_kwargs) -> list[dict]:
        return [{"id": "echo-1", "name": "echo.txt", "mimeType": "text/plain"}]

    def import_documents(self, **_kwargs) -> ImportResult:
        doc = ImportedDocument(
            filename="echo.txt",
            format="txt",
            data=b"echo",
            external_id="echo-1",
            revision_id="1",
            etag="",
        )
        return ImportResult(documents=[doc], source_description="echo test provider")
