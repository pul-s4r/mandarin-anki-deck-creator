from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from anki_deck_generator.cli_handlers.common import apply_run_like_settings


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
    run.add_argument(
        "--llm-fixture-path",
        type=Path,
        default=None,
        help="Deterministic LLM fixture JSON (tests; or set ANKI_PIPELINE_LLM_FIXTURE_PATH)",
    )
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
    sched.add_argument("--output", "-o", type=Path, default=None, help="Export vocabulary CSV path (required if source set has no exporters)")
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
    sched.add_argument(
        "--to-anki",
        action="store_true",
        help="Push cards to desktop Anki via AnkiConnect after sync (requires Anki running)",
    )
    sched.add_argument(
        "--anki-deck-name",
        type=str,
        default=None,
        help="Target Anki deck (required with --to-anki unless type: anki is in YAML exporters)",
    )
    sched.add_argument(
        "--anki-model-name",
        type=str,
        default="Chinese vocabulary",
        help="Anki note type name (default: Chinese vocabulary)",
    )
    sched.add_argument(
        "--anki-connect-url",
        type=str,
        default="http://127.0.0.1:8765",
        help="AnkiConnect base URL (default: http://127.0.0.1:8765)",
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

    serve = sub.add_parser("serve", help="Start the HTTP API server (requires [server] extra)")
    serve.add_argument("--host", type=str, default=None, help="Bind host (default: ANKI_SERVER_HOST or 0.0.0.0)")
    serve.add_argument("--port", type=int, default=None, help="Bind port (default: ANKI_SERVER_PORT or 8000)")
    serve.add_argument("-v", "--verbose", action="store_true")

    agent = sub.add_parser("agent", help="Manage the desktop AnkiWeb pull agent")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    setup = agent_sub.add_parser("setup", help="Register agent token and install background service")
    setup.add_argument("--server-url", type=str, default=None)
    setup.add_argument("--agent-id", type=str, default=None)
    setup.add_argument("--register-secret", type=str, default=None)
    setup.add_argument("--anki-connect-url", type=str, default="http://127.0.0.1:8765")
    setup.add_argument("--deck-name", type=str, default="Chinese vocabulary")
    setup.add_argument("--model-name", type=str, default="Chinese vocabulary")
    agent_sub.add_parser("status", help="Show local agent cache/cursor status")
    uninstall = agent_sub.add_parser("uninstall", help="Remove init unit, venv, and cache")
    uninstall.add_argument("--purge", action="store_true", help="Also delete agent.toml")
    agent_sub.add_parser("rebuild-venv", help="Recreate the isolated agent virtualenv")
    revoke = agent_sub.add_parser("revoke", help="Revoke the cloud agent token")
    revoke.add_argument("--register-secret", type=str, default=None)

    # ── drive (M8) ──────────────────────────────────────────────────────── #
    drive = sub.add_parser("drive", help="Drive watch channels, webhook simulation, and pending-edit processing")
    drive_sub = drive.add_subparsers(dest="drive_command", required=True)

    # drive watch
    watch = drive_sub.add_parser("watch", help="Manage Drive watch channels")
    watch_sub = watch.add_subparsers(dest="watch_command", required=True)

    watch_reg = watch_sub.add_parser("register", help="Register a Drive watch channel")
    watch_reg.add_argument("--source-set", type=str, required=True, help="Source set name")
    watch_reg.add_argument("--webhook-url", type=str, required=True, help="HTTPS webhook endpoint URL")
    watch_reg.add_argument("--credentials-file", type=str, required=True, help="OAuth token JSON path")
    watch_reg.add_argument("--state-db", type=Path, required=True, help="SQLite state database path")
    watch_reg.add_argument("--user-id", type=str, default="default")

    watch_unreg = watch_sub.add_parser("unregister", help="Stop and remove a Drive watch channel")
    watch_unreg.add_argument("--channel-id", type=str, required=True, help="Channel ID to unregister")
    watch_unreg.add_argument("--credentials-file", type=str, required=True, help="OAuth token JSON path")
    watch_unreg.add_argument("--state-db", type=Path, required=True, help="SQLite state database path")
    watch_unreg.add_argument("--user-id", type=str, default="default")

    watch_renew = watch_sub.add_parser("renew", help="Renew channels expiring in <48 h")
    watch_renew.add_argument("--webhook-url", type=str, required=True, help="HTTPS webhook endpoint URL")
    watch_renew.add_argument("--credentials-file", type=str, required=True, help="OAuth token JSON path")
    watch_renew.add_argument("--state-db", type=Path, required=True, help="SQLite state database path")
    watch_renew.add_argument("--user-id", type=str, default="default")

    # drive webhook
    webhook = drive_sub.add_parser("webhook", help="Simulate or inspect Drive webhook notifications")
    webhook_sub = webhook.add_subparsers(dest="webhook_command", required=True)

    webhook_sim = webhook_sub.add_parser("simulate", help="Simulate a Drive webhook notification locally")
    webhook_sim.add_argument("--channel-id", type=str, required=True, help="Channel ID to simulate")
    webhook_sim.add_argument("--state", type=str, default="change",
                              choices=["sync", "change", "update", "exists", "remove"],
                              help="X-Goog-Resource-State to simulate")
    webhook_sim.add_argument("--state-db", type=Path, required=True, help="SQLite state database path")

    # drive process-pending
    proc = drive_sub.add_parser("process-pending", help="Run Mode B: process settled pending edits (D7)")
    proc.add_argument("--source-set", type=str, required=True, help="Source set name")
    proc.add_argument("--state-db", type=Path, required=True, help="SQLite state database path")
    proc.add_argument("--source-set-config", type=Path, default=None,
                       help="YAML source-set config (required for actual sync)")
    proc.add_argument("--cedict-path", type=Path, default=None)
    proc.add_argument("--llm-fixture-path", type=Path, default=None)
    proc.add_argument("--user-id", type=str, default="default")

    return p


# Re-export for tests or callers that patch apply_run_like_settings
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
        from anki_deck_generator.cli_handlers.run import run_run_command

        return run_run_command(args)
    if args.command == "auth":
        from anki_deck_generator.cli_handlers.auth import run_auth_command

        return run_auth_command(args)
    if args.command == "state":
        from anki_deck_generator.cli_handlers.state import run_state_command

        return run_state_command(args)
    if args.command == "schedule":
        from anki_deck_generator.cli_handlers.schedule import run_schedule_command

        return run_schedule_command(args)
    if args.command == "import":
        from anki_deck_generator.cli_handlers.import_command import run_import_command

        return run_import_command(args)
    if args.command == "serve":
        from anki_deck_generator.cli_handlers.serve import run_serve_command

        return run_serve_command(args)
    if args.command == "agent":
        from anki_deck_generator.cli_handlers.agent import run_agent_command

        return run_agent_command(args)
    if args.command == "drive":
        from anki_deck_generator.cli_handlers.drive import run_drive_command

        return run_drive_command(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
