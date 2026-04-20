from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from anki_deck_generator.pipeline import PipelineResult


class Exporter(Protocol):
    """Serialize a pipeline result to bytes (CSV, XLSX, etc.)."""

    def export(self, result: PipelineResult) -> bytes: ...

    @property
    def filename_suggestion(self) -> str: ...
