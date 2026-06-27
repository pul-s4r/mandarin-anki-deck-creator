"""Build schedule exporters from source-set configuration and CLI flags."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from anki_deck_generator.config.source_sets import (
    AnkiExporterConfig,
    CsvExporterConfig,
    ExporterConfig,
    SourceSet,
    XlsxExporterConfig,
)
from anki_deck_generator.export.anki_direct import VocabularyAnkiDirectExporter
from anki_deck_generator.export.exporters import VocabularyCsvFileExporter, VocabularyXlsxFileExporter
from anki_deck_generator.export.file_target import FileTargetExporter

if TYPE_CHECKING:
    from typing import TypeAlias

    ScheduleExporter: TypeAlias = FileTargetExporter | VocabularyAnkiDirectExporter
else:
    ScheduleExporter = object


def anki_config_to_exporter(cfg: AnkiExporterConfig) -> VocabularyAnkiDirectExporter:
    return VocabularyAnkiDirectExporter(
        deck_name=cfg.deck_name,
        model_name=cfg.model_name,
        anki_connect_url=cfg.anki_connect_url,
        anki_connect_api_key=cfg.anki_connect_api_key,
        conflict_policy=cfg.conflict_policy,
        auto_create_deck=cfg.auto_create_deck,
        auto_create_model=cfg.auto_create_model,
        auto_sync=cfg.auto_sync,
    )


def build_schedule_exporters_from_configs(
    configs: tuple[ExporterConfig, ...],
    *,
    csv_bom: bool = False,
) -> list[ScheduleExporter]:
    exporters: list[ScheduleExporter] = []
    for cfg in configs:
        if isinstance(cfg, CsvExporterConfig):
            dest = Path(cfg.destination).expanduser().resolve()
            exporters.append(VocabularyCsvFileExporter(output_path=dest, bom=csv_bom))
        elif isinstance(cfg, XlsxExporterConfig):
            dest = Path(cfg.destination).expanduser().resolve()
            exporters.append(VocabularyXlsxFileExporter(output_path=dest))
        elif isinstance(cfg, AnkiExporterConfig):
            exporters.append(anki_config_to_exporter(cfg))
        else:  # pragma: no cover - exhaustive union
            raise TypeError(f"Unsupported exporter config: {type(cfg).__name__}")
    return exporters


def build_file_exporters_from_configs(
    configs: tuple[ExporterConfig, ...],
    *,
    csv_bom: bool = False,
) -> list[FileTargetExporter]:
    """Return only file-target exporters (csv/xlsx) from *configs*."""
    out: list[FileTargetExporter] = []
    for exp in build_schedule_exporters_from_configs(configs, csv_bom=csv_bom):
        if isinstance(exp, VocabularyAnkiDirectExporter):
            continue
        out.append(exp)
    return out


def resolve_exporters_for_schedule(
    source_set: SourceSet,
    *,
    cli_output: Path | None,
    csv_bom: bool = False,
    cli_anki: AnkiExporterConfig | None = None,
) -> list[ScheduleExporter]:
    """Use YAML exporters when configured; otherwise fall back to CLI flags."""
    exporters: list[ScheduleExporter] = []
    if source_set.exporters:
        exporters.extend(build_schedule_exporters_from_configs(source_set.exporters, csv_bom=csv_bom))
    elif cli_output is not None:
        exporters.append(
            VocabularyCsvFileExporter(output_path=cli_output.resolve(), bom=csv_bom)
        )
    if cli_anki is not None and not any(isinstance(e, VocabularyAnkiDirectExporter) for e in exporters):
        exporters.append(anki_config_to_exporter(cli_anki))
    if not exporters:
        raise ValueError(
            "Configure exporters in YAML, pass --output for CSV, or pass --to-anki with --anki-deck-name"
        )
    return exporters
