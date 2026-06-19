"""Build file-target exporters from source-set configuration."""

from __future__ import annotations

from pathlib import Path

from anki_deck_generator.config.source_sets import (
    CsvExporterConfig,
    ExporterConfig,
    SourceSet,
    XlsxExporterConfig,
)
from anki_deck_generator.export.exporters import VocabularyCsvFileExporter, VocabularyXlsxFileExporter
from anki_deck_generator.export.file_target import FileTargetExporter


def build_file_exporters_from_configs(
    configs: tuple[ExporterConfig, ...],
    *,
    csv_bom: bool = False,
) -> list[FileTargetExporter]:
    exporters: list[FileTargetExporter] = []
    for cfg in configs:
        dest = Path(cfg.destination).expanduser().resolve()
        if isinstance(cfg, CsvExporterConfig):
            exporters.append(VocabularyCsvFileExporter(output_path=dest, bom=csv_bom))
        elif isinstance(cfg, XlsxExporterConfig):
            exporters.append(VocabularyXlsxFileExporter(output_path=dest))
        else:  # pragma: no cover - exhaustive union
            raise TypeError(f"Unsupported exporter config: {type(cfg).__name__}")
    return exporters


def resolve_exporters_for_schedule(
    source_set: SourceSet,
    *,
    cli_output: Path | None,
    csv_bom: bool = False,
) -> list[FileTargetExporter]:
    """Use YAML exporters when configured; otherwise fall back to CLI --output CSV."""
    if source_set.exporters:
        return build_file_exporters_from_configs(source_set.exporters, csv_bom=csv_bom)
    if cli_output is None:
        raise ValueError(
            "--output is required when the source set defines no exporters in YAML"
        )
    return [VocabularyCsvFileExporter(output_path=cli_output.resolve(), bom=csv_bom)]
