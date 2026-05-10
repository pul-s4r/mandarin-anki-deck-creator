"""`schedule` subcommand handler."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from anki_deck_generator.cli_handlers.common import apply_run_like_settings
from anki_deck_generator.config.settings import Settings
from anki_deck_generator.config.source_sets import load_source_sets_yaml, pick_source_set
from anki_deck_generator.errors import AnkiPipelineError
from anki_deck_generator.export.exporters import VocabularyCsvFileExporter
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.sync.orchestrator import run_incremental_sync


def run_schedule_command(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.source_set_config is not None:
        settings.source_set_config = args.source_set_config
    cfg_path = settings.source_set_config
    if cfg_path is None:
        print("error: pass --source-set-config or set ANKI_PIPELINE_SOURCE_SET_CONFIG", file=sys.stderr)
        return 1
    apply_run_like_settings(settings, args)
    settings.state_backend = "sqlite"
    settings.state_db_path = Path(args.state_db).resolve()

    store = SqliteStateStore(settings.state_db_path)
    store.init_schema()
    try:
        sets = load_source_sets_yaml(Path(cfg_path).resolve())
        sset = pick_source_set(sets, args.source_set)
        report = run_incremental_sync(
            sset,
            settings=settings,
            state_store=store,
            exporters=[VocabularyCsvFileExporter(output_path=Path(args.output).resolve(), bom=settings.csv_bom)],
        )
    except AnkiPipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    print(json.dumps(report.to_jsonable(), indent=2))
    return 0
