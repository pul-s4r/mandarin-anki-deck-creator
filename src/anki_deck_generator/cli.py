from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from anki_deck_generator.cli_handlers import (
    apply_run_like_settings,
    run_auth_command,
    run_import_command,
    run_run_command,
    run_schedule_command,
    run_state_command,
)


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
    sched.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would change (metadata only for Drive); no LLM, DB writes, or export",
    )
    sched.add_argument("-v", "--verbose", action="store_true")

    auth = sub.add_parser("auth", help="Authenticate an integration (e.g. OAuth for Google Drive)")
    auth_sub = auth.add_subparsers(dest="auth_provider", required=True)
    auth_gd = auth_sub.add_parser(
        "google-drive",
        help="Browser OAuth flow (drive.readonly); saves token JSON for schedule/import",
    )
    auth_gd.add_argument(
        "--client-secrets",
        type=Path,
        required=True,
        help="Google OAuth client secrets JSON (Desktop app)",
    )
    auth_gd.add_argument(
        "--token-file",
        type=Path,
        default=None,
        help="Where to write credentials JSON (default: XDG config path)",
    )

    imp = sub.add_parser("import", help="Import from an external source provider (optional integrations)")
    imp.add_argument(
        "--list-providers",
        action="store_true",
        help="List registered provider names and exit",
    )
    imp.add_argument(
        "provider",
        nargs="?",
        help="Provider name (e.g. echo, google-drive); omit with --list-providers",
    )
    imp.add_argument("--folder-id", default=None, help="Google Drive folder id (google-drive)")
    imp.add_argument(
        "--file-id",
        dest="file_ids",
        action="append",
        default=None,
        help="Google Drive file id (repeatable; google-drive)",
    )
    imp.add_argument(
        "--credentials-file",
        type=Path,
        default=None,
        help="OAuth token JSON or service-account key (google-drive)",
    )
    imp.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output directory for downloaded files (google-drive)",
    )

    return p


__all__ = ["main", "apply_run_like_settings", "_build_parser"]


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "run":
        return run_run_command(args)
    if args.command == "auth":
        return run_auth_command(args)
    if args.command == "state":
        return run_state_command(args)
    if args.command == "schedule":
        return run_schedule_command(args)
    if args.command == "import":
        return run_import_command(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
