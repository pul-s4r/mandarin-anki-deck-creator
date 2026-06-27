"""`schedule` subcommand handler."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from anki_deck_generator.cli_handlers.common import apply_run_like_settings
from anki_deck_generator.config.settings import Settings
from anki_deck_generator.config.source_sets import AnkiExporterConfig, load_source_sets_yaml, pick_source_set
from anki_deck_generator.errors import AnkiPipelineError
from anki_deck_generator.export.exporter_factory import resolve_exporters_for_schedule
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
        cli_anki: AnkiExporterConfig | None = None
        if args.to_anki:
            if not args.anki_deck_name:
                print("error: --to-anki requires --anki-deck-name", file=sys.stderr)
                return 1
            cli_anki = AnkiExporterConfig(
                type="anki",
                deck_name=args.anki_deck_name,
                model_name=args.anki_model_name,
                anki_connect_url=args.anki_connect_url,
            )
        try:
            exporters = resolve_exporters_for_schedule(
                sset,
                cli_output=Path(args.output).resolve() if args.output is not None else None,
                csv_bom=settings.csv_bom,
                cli_anki=cli_anki,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        report = run_incremental_sync(
            sset,
            settings=settings,
            state_store=store,
            exporters=exporters,
            dry_run=bool(args.dry_run),
        )
    except AnkiPipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    print(json.dumps(report.to_jsonable(), indent=2))
    return 0
