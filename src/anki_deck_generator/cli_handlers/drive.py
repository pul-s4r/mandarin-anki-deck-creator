"""CLI handlers for Drive watch, webhook simulation, and process-pending commands."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _make_state_store(args: argparse.Namespace):
    from anki_deck_generator.state.sqlite_store import SqliteStateStore

    db_path = Path(args.state_db)
    store = SqliteStateStore(db_path)
    store.init_schema()
    return store


def run_drive_command(args: argparse.Namespace) -> int:
    sub = getattr(args, "drive_command", None)
    if sub == "watch":
        return _run_drive_watch(args)
    if sub == "webhook":
        return _run_drive_webhook(args)
    if sub == "process-pending":
        return _run_process_pending(args)
    print(f"Unknown drive command: {sub!r}")
    return 1


# ──────────────────────────── watch sub-commands ──────────────────────── #


def _run_drive_watch(args: argparse.Namespace) -> int:
    watch_sub = getattr(args, "watch_command", None)
    if watch_sub == "register":
        return _watch_register(args)
    if watch_sub == "unregister":
        return _watch_unregister(args)
    if watch_sub == "renew":
        return _watch_renew(args)
    print(f"Unknown watch command: {watch_sub!r}")
    return 1


def _watch_register(args: argparse.Namespace) -> int:
    from anki_deck_generator.sync.drive_watch import register_watch_channel

    store = _make_state_store(args)
    rec = register_watch_channel(
        credentials_file=args.credentials_file,
        source_set_name=args.source_set,
        webhook_url=args.webhook_url,
        state_store=store,
        user_id=getattr(args, "user_id", "default"),
    )
    print(f"Registered channel: {rec.channel_id}")
    print(f"  resource_id : {rec.resource_id}")
    print(f"  page_token  : {rec.page_token}")
    print(f"  expiration  : {rec.expiration}")
    return 0


def _watch_unregister(args: argparse.Namespace) -> int:
    from anki_deck_generator.sync.drive_watch import unregister_watch_channel

    store = _make_state_store(args)
    unregister_watch_channel(
        channel_id=args.channel_id,
        credentials_file=args.credentials_file,
        state_store=store,
        user_id=getattr(args, "user_id", "default"),
    )
    print(f"Unregistered channel: {args.channel_id}")
    return 0


def _watch_renew(args: argparse.Namespace) -> int:
    from anki_deck_generator.sync.drive_watch import renew_expiring_channels

    store = _make_state_store(args)
    renewed = renew_expiring_channels(
        credentials_file=args.credentials_file,
        webhook_url=args.webhook_url,
        state_store=store,
        user_id=getattr(args, "user_id", "default"),
    )
    if renewed:
        print(f"Renewed {len(renewed)} channel(s): {', '.join(renewed)}")
    else:
        print("No channels needed renewal.")
    return 0


# ──────────────────────── webhook simulation ──────────────────────────── #


def _run_drive_webhook(args: argparse.Namespace) -> int:
    webhook_sub = getattr(args, "webhook_command", None)
    if webhook_sub == "simulate":
        return _webhook_simulate(args)
    print(f"Unknown webhook command: {webhook_sub!r}")
    return 1


def _webhook_simulate(args: argparse.Namespace) -> int:
    """Simulate a Drive webhook notification locally (no HTTP call needed)."""
    from anki_deck_generator.sync.drive_events import enqueue_mode_a, drain_mode_a_queue

    channel_id = args.channel_id
    resource_state = getattr(args, "state", "change")
    store = _make_state_store(args)

    print(f"Simulating Drive webhook: channel={channel_id!r}, state={resource_state!r}")

    if resource_state == "sync":
        print("State 'sync' is acknowledgement-only; no action taken.")
        return 0

    if resource_state in {"change", "update", "exists"}:
        enqueue_mode_a(channel_id)
        processed = drain_mode_a_queue(state_store=store)
        print(f"Mode A triggered; processed channels: {processed}")
        return 0

    if resource_state == "remove":
        print("State 'remove': channel expired/removed; trigger renewal manually.")
        return 0

    print(f"Unhandled state: {resource_state!r}")
    return 1


# ──────────────────────── process-pending (Mode B) ─────────────────────── #


def _run_process_pending(args: argparse.Namespace) -> int:
    from anki_deck_generator.config.settings import Settings
    from anki_deck_generator.config.source_sets import load_source_sets_yaml, pick_source_set
    from anki_deck_generator.export.exporter_factory import build_file_exporters_from_configs
    from anki_deck_generator.sync.drive_events import process_pending

    store = _make_state_store(args)

    settings = Settings()
    if getattr(args, "llm_fixture_path", None):
        settings = settings.model_copy(update={"llm_fixture_path": Path(args.llm_fixture_path)})
    if getattr(args, "cedict_path", None):
        settings = settings.model_copy(update={"cedict_path": Path(args.cedict_path)})

    source_set_cfg_path = getattr(args, "source_set_config", None)
    source_set = None
    exporters = []
    if source_set_cfg_path and args.source_set:
        config = load_source_sets_yaml(Path(source_set_cfg_path))
        source_set = pick_source_set(config, args.source_set)
        exporters = build_file_exporters_from_configs(source_set.exporters, csv_bom=settings.csv_bom)

    count = process_pending(
        state_store=store,
        source_set_name=args.source_set,
        settings=settings,
        exporters=exporters,
        user_id=getattr(args, "user_id", "default"),
        source_set=source_set,
    )
    print(f"Mode B: processed {count} pending file(s) for source_set {args.source_set!r}")
    return 0
