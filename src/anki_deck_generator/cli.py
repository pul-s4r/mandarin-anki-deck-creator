from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.errors import AnkiPipelineError, IntegrationError
from anki_deck_generator.pipeline import run_pipeline


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="anki-notes-pipeline", description="Chinese notes → Anki vocabulary CSV")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run extraction pipeline")
    run.add_argument("input", type=Path, help="Input PDF, Markdown, or DOCX file")
    run.add_argument("--output", "-o", type=Path, required=True, help="Output CSV path")
    run.add_argument("--cedict-path", type=Path, default=None, help="Path to cedict_ts.u8")
    run.add_argument("--prior-csv", type=Path, default=None, help="Optional prior exported CSV for term index")
    run.add_argument("--sentence-links-csv", type=Path, default=None, help="Write sentence_links.csv to this path")
    run.add_argument(
        "--enable-sentences",
        dest="enable_sentences",
        action="store_true",
        help="Enable dialogue sentence parsing + linking (default)",
    )
    run.add_argument(
        "--disable-sentences",
        dest="enable_sentences",
        action="store_false",
        help="Disable dialogue sentence parsing + linking",
    )
    run.add_argument(
        "--sentence-assignment-strategy",
        choices=["importance", "random"],
        default=None,
        help="When multiple terms match a sentence, pick winner by 'importance' (default) or 'random'",
    )
    run.add_argument("--sentence-random-seed", type=int, default=None, help="Seed for random sentence assignment")
    run.add_argument(
        "--sentences-per-term",
        type=int,
        default=None,
        help="Max number of sentences to store per term in the main CSV (default 1)",
    )
    run.add_argument(
        "--sentences-delimiter",
        type=str,
        default=None,
        help="Delimiter when storing multiple sentences per term (default: ' | ')",
    )
    run.add_argument("--chunk-size", type=int, default=None)
    run.add_argument("--chunk-overlap", type=int, default=None)
    run.add_argument("--csv-bom", action="store_true", help="Write UTF-8 BOM for Excel")
    run.add_argument("--no-skip-lines-filter", action="store_true", help="Disable date-only line dropping")
    run.add_argument("--cedict-force-overwrite", action="store_true", help="Overwrite LLM meaning/pinyin from CEDICT")
    run.add_argument(
        "--no-decomposition-fallback",
        dest="enable_decomposition_fallback",
        action="store_false",
        help="Disable greedy CEDICT decomposition when exact headword is missing",
    )
    run.add_argument(
        "--no-llm-translation-fallback",
        dest="enable_llm_translation_fallback",
        action="store_false",
        help="Disable Bedrock batch translation for rows still missing English after enrichment",
    )
    run.set_defaults(
        enable_sentences=True,
        enable_decomposition_fallback=True,
        enable_llm_translation_fallback=True,
    )
    run.add_argument("-v", "--verbose", action="store_true")

    st = sub.add_parser("state", help="Manage local SQLite state database")
    st_sub = st.add_subparsers(dest="state_command", required=True)
    st_init = st_sub.add_parser("init", help="Create state database and schema")
    st_init.add_argument("--db-path", type=Path, required=True)
    st_list = st_sub.add_parser("list-cards", help="List vocabulary cards in state")
    st_list.add_argument("--db-path", type=Path, required=True)
    st_runs = st_sub.add_parser("list-runs", help="List recent sync runs")
    st_runs.add_argument("--db-path", type=Path, required=True)

    sched = sub.add_parser("schedule", help="Run incremental sync for a configured source set")
    sched.add_argument("--source-set", type=str, required=True, help="Name of the source set in the YAML config")
    sched.add_argument("--state-db", type=Path, required=True, help="SQLite state database path")
    sched.add_argument(
        "--source-set-config",
        type=Path,
        default=None,
        help="YAML file (default: ANKI_PIPELINE_SOURCE_SET_CONFIG)",
    )
    sched.add_argument("--output", "-o", type=Path, required=True, help="Export vocabulary CSV path")
    sched.add_argument("--cedict-path", type=Path, default=None)
    sched.add_argument("--llm-fixture-path", type=Path, default=None, help="Deterministic LLM fixture JSON (tests)")
    sched.add_argument("--chunk-size", type=int, default=None)
    sched.add_argument("--chunk-overlap", type=int, default=None)
    sched.add_argument("--csv-bom", action="store_true")
    sched.add_argument("--no-skip-lines-filter", action="store_true")
    sched.add_argument("--disable-sentences", dest="enable_sentences", action="store_false")
    sched.set_defaults(enable_sentences=False)
    sched.add_argument("-v", "--verbose", action="store_true")

    imp = sub.add_parser("import", help="Import from an external source provider (optional integrations)")
    imp.add_argument(
        "--list-providers",
        action="store_true",
        help="List registered provider names and exit",
    )
    imp.add_argument(
        "provider",
        nargs="?",
        help="Provider name (e.g. echo); omit with --list-providers",
    )

    return p


def _run_state_command(args: argparse.Namespace) -> int:
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


def _run_schedule_command(args: argparse.Namespace) -> int:
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
    _apply_run_like_settings(settings, args)
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


def _run_import_command(args: argparse.Namespace) -> int:
    import importlib

    from anki_deck_generator.integrations.registry import available_providers, get_provider

    importlib.import_module("anki_deck_generator.integrations.echo")
    if args.list_providers:
        for name in available_providers():
            print(name)
        return 0
    if not args.provider:
        print("error: pass a provider name or --list-providers", file=sys.stderr)
        return 1
    try:
        provider = get_provider(args.provider)
    except IntegrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        provider.authenticate({})
        result = provider.import_documents()
    except AnkiPipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.source_description)
    for doc in result.documents:
        print(f"  {doc.filename}\t{doc.format}\t{len(doc.data)} bytes\tid={doc.external_id}")
    return 0


def _apply_run_like_settings(settings: Settings, args: argparse.Namespace) -> None:
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


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "run":
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
        _apply_run_like_settings(settings, args)
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

    if args.command == "state":
        return _run_state_command(args)

    if args.command == "schedule":
        return _run_schedule_command(args)

    if args.command == "import":
        return _run_import_command(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
