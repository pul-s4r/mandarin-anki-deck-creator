"""CLI subcommand handlers (testable without going through argparse main)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.errors import AnkiPipelineError
from anki_deck_generator.pipeline import run_pipeline


def apply_run_like_settings(settings: Settings, args: argparse.Namespace) -> None:
    if getattr(args, "cedict_path", None) is not None:
        settings.cedict_path = args.cedict_path
    if getattr(args, "chunk_size", None) is not None:
        settings.chunk_size = args.chunk_size
    if getattr(args, "chunk_overlap", None) is not None:
        settings.chunk_overlap = args.chunk_overlap
    if getattr(args, "csv_bom", False):
        settings.csv_bom = True
    if getattr(args, "no_skip_lines_filter", False):
        settings.skip_lines_filter = False
    if getattr(args, "llm_fixture_path", None) is not None:
        settings.llm_fixture_path = args.llm_fixture_path
    if hasattr(args, "enable_sentences"):
        settings.enable_sentences = bool(args.enable_sentences)


def run_run_command(args: argparse.Namespace) -> int:
    settings = Settings()
    settings.enable_sentences = bool(args.enable_sentences)
    settings.enable_decomposition_fallback = bool(args.enable_decomposition_fallback)
    settings.enable_llm_translation_fallback = bool(args.enable_llm_translation_fallback)
    if args.prior_csv is not None:
        settings.prior_csv = args.prior_csv
    if args.sentence_links_csv is not None:
        settings.sentence_links_csv = args.sentence_links_csv
    if args.sentence_assignment_strategy is not None:
        settings.sentence_assignment_strategy = args.sentence_assignment_strategy
    if args.sentence_random_seed is not None:
        settings.sentence_random_seed = args.sentence_random_seed
    if args.sentences_per_term is not None:
        settings.sentences_per_term = args.sentences_per_term
    if args.sentences_delimiter is not None:
        settings.sentences_delimiter = args.sentences_delimiter
    apply_run_like_settings(settings, args)
    if args.cedict_force_overwrite:
        settings.cedict_force_overwrite = True
    if not args.enable_decomposition_fallback:
        settings.enable_decomposition_fallback = False
    if not args.enable_llm_translation_fallback:
        settings.enable_llm_translation_fallback = False
    try:
        run_pipeline(args.input.resolve(), args.output.resolve(), settings)
    except AnkiPipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def run_state_command(args: argparse.Namespace) -> int:
    from anki_deck_generator.state.sqlite_store import SqliteStateStore

    db = Path(args.db_path).resolve()
    if args.state_command == "init":
        store = SqliteStateStore(db)
        store.init_schema()
        store.close()
        print(f"initialized state database at {db}")
        return 0
    if args.state_command == "list-cards":
        store = SqliteStateStore(db)
        try:
            rows = list(store.iter_all_cards())
        finally:
            store.close()
        if not rows:
            print("(no cards)")
            return 0
        for r in rows:
            m = (r.meaning or "")[:48]
            print(f"{r.simplified}\t{r.card_id[:8]}…\t{m}")
        return 0
    if args.state_command == "list-runs":
        store = SqliteStateStore(db)
        try:
            runs = list(store.iter_runs(limit=50))
        finally:
            store.close()
        if not runs:
            print("(no runs)")
            return 0
        for rr in runs:
            print(f"{rr.run_id}\t{rr.trigger}\t{rr.started_at}\tjson_bytes={len(rr.sync_report_json)}")
        return 0
    return 1


def run_schedule_command(args: argparse.Namespace) -> int:
    from anki_deck_generator.config.source_sets import load_source_sets_yaml, pick_source_set
    from anki_deck_generator.export.exporters import VocabularyCsvFileExporter
    from anki_deck_generator.state.sqlite_store import SqliteStateStore
    from anki_deck_generator.sync.orchestrator import run_incremental_sync

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
