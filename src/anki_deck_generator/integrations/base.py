"""Abstract integration provider and document types for external sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ImportedDocument:
    """A single document fetched from an external source."""

    filename: str
    format: str  # "pdf" | "markdown" | "docx" | "txt"
    data: bytes
    external_id: str
    revision_id: str = ""
    etag: str = ""


@dataclass
class ImportResult:
    """Outcome of an import operation."""

    documents: list[ImportedDocument]
    source_description: str


class IntegrationProvider(ABC):
    """Base class for external source integrations (Drive, S3, etc.)."""

    name: str

    @abstractmethod
    def authenticate(self, credentials: dict) -> None:
        """Set up authentication (OAuth, API keys, service accounts)."""

    @abstractmethod
    def list_sources(self, **kwargs) -> list[dict]:
        """List documents or containers the user can import from."""

    @abstractmethod
    def import_documents(self, **kwargs) -> ImportResult:
        """Fetch one or more documents and return their raw bytes."""
