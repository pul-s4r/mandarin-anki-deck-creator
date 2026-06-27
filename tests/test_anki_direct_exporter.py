"""Tests for direct desktop Anki export (local delivery mode)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from anki_deck_generator.config.source_sets import (
    AnkiExporterConfig,
    CsvExporterConfig,
    LocalFileSource,
    SourceSet,
    load_source_sets_yaml,
)
from anki_deck_generator.export.exporter_factory import (
    build_schedule_exporters_from_configs,
    resolve_exporters_for_schedule,
)
from anki_deck_generator.export.anki_direct import VocabularyAnkiDirectExporter
from anki_deck_generator.export.exporters import VocabularyCsvFileExporter
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.sync.orchestrator import run_incremental_sync


def test_parse_anki_exporter_from_yaml(tmp_path: Path) -> None:
    yml = tmp_path / "sources.yaml"
    yml.write_text(
        """
schema_version: 1
source_sets:
  lessons:
    sources:
      - provider: local-filesystem
        path: /tmp/notes.md
    exporters:
      - type: anki
        deck_name: "Chinese::301"
        model_name: "My model"
""",
        encoding="utf-8",
    )
    cfg = load_source_sets_yaml(yml)
    ss = cfg["lessons"]
    assert len(ss.exporters) == 1
    exp_cfg = ss.exporters[0]
    assert isinstance(exp_cfg, AnkiExporterConfig)
    assert exp_cfg.deck_name == "Chinese::301"
    assert exp_cfg.model_name == "My model"


def test_resolve_exporters_to_anki_cli_flag(tmp_path: Path) -> None:
    md = tmp_path / "n.md"
    md.write_text("x", encoding="utf-8")
    sset = SourceSet(
        name="t",
        sources=(LocalFileSource(provider="local-filesystem", path=md, external_id="e"),),
    )
    cli_anki = AnkiExporterConfig(type="anki", deck_name="Deck")
    exporters = resolve_exporters_for_schedule(sset, cli_output=None, cli_anki=cli_anki)
    assert len(exporters) == 1
    assert isinstance(exporters[0], VocabularyAnkiDirectExporter)
    assert exporters[0].deck_name == "Deck"


def test_resolve_exporters_to_anki_requires_deck_name_via_cli(tmp_path: Path) -> None:
    md = tmp_path / "n.md"
    md.write_text("x", encoding="utf-8")
    sset = SourceSet(
        name="t",
        sources=(LocalFileSource(provider="local-filesystem", path=md, external_id="e"),),
    )
    with pytest.raises(ValueError, match="Configure exporters"):
        resolve_exporters_for_schedule(sset, cli_output=None, cli_anki=None)


def test_build_schedule_exporters_mixed_csv_and_anki(tmp_path: Path) -> None:
    csv_out = tmp_path / "deck.csv"
    configs = (
        CsvExporterConfig(type="csv", destination=csv_out),
        AnkiExporterConfig(type="anki", deck_name="D"),
    )
    exporters = build_schedule_exporters_from_configs(configs)
    assert len(exporters) == 2
    assert isinstance(exporters[0], VocabularyCsvFileExporter)
    assert isinstance(exporters[1], VocabularyAnkiDirectExporter)


def test_incremental_sync_applies_anki_direct_exporter(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1] / "tests" / "baselines"
    md_path = root / "inputs" / "sample.md"
    settings_path = root / "settings.env"
    from anki_deck_generator.config.settings import Settings

    settings = Settings(
        llm_fixture_path=root / "llm_mock.json",
        cedict_path=root / "cedict_sample.u8",
        enable_sentences=False,
        skip_lines_filter=False,
    )
    _ = settings_path  # fixture reference only

    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()
    ext_id = str(md_path.resolve())
    sset = SourceSet(
        name="lessons",
        sources=(LocalFileSource(provider="local-filesystem", path=md_path, external_id=ext_id),),
    )
    anki_exp = VocabularyAnkiDirectExporter(deck_name="TestDeck")
    from anki_deck_generator.sync.report import AnkiWebExportReport

    anki_report = AnkiWebExportReport(
        agent_id="local-direct",
        batch_id="run",
        created=3,
        updated=1,
        sync_requested=True,
        sync_status="ok",
        exporter="anki",
    )

    with patch.object(VocabularyAnkiDirectExporter, "apply", return_value=anki_report) as apply_mock:
        report = run_incremental_sync(
            sset,
            settings=settings,
            state_store=store,
            exporters=[anki_exp],
        )

    apply_mock.assert_called_once()
    assert "anki" in report.exports
    assert len(report.exports["anki"]) == 1
    assert report.exports["anki"][0].created == 3
