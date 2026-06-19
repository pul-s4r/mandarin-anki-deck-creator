from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from anki_deck_generator.export.csv_writer import vocabulary_csv_bytes
from anki_deck_generator.export.sentence_links import sentence_links_csv_bytes
from anki_deck_generator.export.xlsx_writer import vocabulary_xlsx_bytes
from anki_deck_generator.pipeline_types import PipelineResult

if TYPE_CHECKING:
    from anki_deck_generator.sync.report import SyncReport


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


class VocabularyXlsxExporter:
    """XLSX vocabulary deck export with optional sync run metadata."""

    def __init__(
        self,
        *,
        sync_report: SyncReport | None = None,
        source_set_name: str | None = None,
    ) -> None:
        self.sync_report = sync_report
        self.source_set_name = source_set_name

    def export(self, result: PipelineResult) -> bytes:
        return vocabulary_xlsx_bytes(
            result.rows,
            sync_report=self.sync_report,
            source_set_name=self.source_set_name,
        )

    @property
    def filename_suggestion(self) -> str:
        return "vocabulary.xlsx"


@dataclass
class VocabularyXlsxFileExporter:
    """XLSX export with a target path (used by incremental sync orchestrator)."""

    output_path: Path
    sync_report: SyncReport | None = None
    source_set_name: str | None = None

    def export(self, result: PipelineResult) -> bytes:
        return vocabulary_xlsx_bytes(
            result.rows,
            sync_report=self.sync_report,
            source_set_name=self.source_set_name,
        )

    @property
    def filename_suggestion(self) -> str:
        return self.output_path.name
