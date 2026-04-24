from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from anki_deck_generator.export.csv_writer import vocabulary_csv_bytes
from anki_deck_generator.export.sentence_links import sentence_links_csv_bytes
from anki_deck_generator.pipeline import PipelineResult


@dataclass
class VocabularyCsvFileExporter:
    """CSV export with a target path (used by incremental sync orchestrator)."""

    output_path: Path
    bom: bool = False

    def export(self, result: PipelineResult) -> bytes:
        return vocabulary_csv_bytes(result.rows, bom=self.bom)

    @property
    def filename_suggestion(self) -> str:
        return self.output_path.name


class VocabularyCsvExporter:
    """CSV vocabulary deck export (main pipeline CSV)."""

    def __init__(self, *, bom: bool = False) -> None:
        self._bom = bom

    def export(self, result: PipelineResult) -> bytes:
        return vocabulary_csv_bytes(result.rows, bom=self._bom)

    @property
    def filename_suggestion(self) -> str:
        return "vocabulary.csv"


class SentenceLinksCsvExporter:
    """Sentence link sidecar CSV."""

    def export(self, result: PipelineResult) -> bytes:
        return sentence_links_csv_bytes(result.sentence_links)

    @property
    def filename_suggestion(self) -> str:
        return "sentence_links.csv"
