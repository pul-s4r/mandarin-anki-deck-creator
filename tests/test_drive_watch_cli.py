"""Tests for drive watch CLI commands (D4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_deck_generator.cli import _build_parser, main
from anki_deck_generator.state.records import DriveChannelRecord
from anki_deck_generator.state.sqlite_store import SqliteStateStore


# ──────────────────────── parser smoke tests ──────────────────────────── #


def test_parser_drive_watch_register() -> None:
    p = _build_parser()
    args = p.parse_args([
        "drive", "watch", "register",
        "--source-set", "my-set",
        "--webhook-url", "https://example.com/webhook",
        "--credentials-file", "/creds.json",
        "--state-db", "/state.db",
    ])
    assert args.command == "drive"
    assert args.drive_command == "watch"
    assert args.watch_command == "register"
    assert args.source_set == "my-set"
    assert args.webhook_url == "https://example.com/webhook"


def test_parser_drive_watch_unregister() -> None:
    p = _build_parser()
    args = p.parse_args([
        "drive", "watch", "unregister",
        "--channel-id", "chan-1",
        "--credentials-file", "/creds.json",
        "--state-db", "/state.db",
    ])
    assert args.watch_command == "unregister"
    assert args.channel_id == "chan-1"


def test_parser_drive_watch_renew() -> None:
    p = _build_parser()
    args = p.parse_args([
        "drive", "watch", "renew",
        "--webhook-url", "https://example.com/wh",
        "--credentials-file", "/creds.json",
        "--state-db", "/state.db",
    ])
    assert args.watch_command == "renew"


def test_parser_drive_webhook_simulate() -> None:
    p = _build_parser()
    args = p.parse_args([
        "drive", "webhook", "simulate",
        "--channel-id", "chan-test",
        "--state", "change",
        "--state-db", "/state.db",
    ])
    assert args.drive_command == "webhook"
    assert args.webhook_command == "simulate"
    assert args.state == "change"


def test_parser_drive_process_pending() -> None:
    p = _build_parser()
    args = p.parse_args([
        "drive", "process-pending",
        "--source-set", "my-set",
        "--state-db", "/state.db",
    ])
    assert args.drive_command == "process-pending"
    assert args.source_set == "my-set"


# ─────────────────── handler tests ──────────────────────────────────────── #


def test_watch_register_handler(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    creds = tmp_path / "creds.json"
    creds.write_text("{}", encoding="utf-8")

    mock_rec = DriveChannelRecord(
        channel_id="new-chan",
        resource_id="res-x",
        page_token="tok-x",
        source_set_name="my-set",
    )

    with patch("anki_deck_generator.sync.drive_watch.register_watch_channel", return_value=mock_rec) as mock_reg:
        ret = main([
            "drive", "watch", "register",
            "--source-set", "my-set",
            "--webhook-url", "https://example.com/wh",
            "--credentials-file", str(creds),
            "--state-db", str(db),
        ])

    assert ret == 0
    mock_reg.assert_called_once()
    _, kwargs = mock_reg.call_args
    assert kwargs["source_set_name"] == "my-set"


def test_watch_unregister_handler(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()
    store.upsert_drive_channel(DriveChannelRecord(
        channel_id="chan-1",
        resource_id="res-1",
        page_token="tok-1",
        source_set_name="my-set",
        channel_token="tkn",
    ))

    with patch("anki_deck_generator.sync.drive_watch.unregister_watch_channel") as mock_unreg:
        ret = main([
            "drive", "watch", "unregister",
            "--channel-id", "chan-1",
            "--credentials-file", str(tmp_path / "creds.json"),
            "--state-db", str(db),
        ])

    assert ret == 0
    mock_unreg.assert_called_once()


def test_webhook_simulate_sync_state(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()

    ret = main([
        "drive", "webhook", "simulate",
        "--channel-id", "some-chan",
        "--state", "sync",
        "--state-db", str(db),
    ])
    assert ret == 0


def test_webhook_simulate_change_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()
    store.upsert_drive_channel(DriveChannelRecord(
        channel_id="chan-sim",
        resource_id="r",
        page_token="tok",
        source_set_name="my-set",
        channel_token="t",
    ))

    from anki_deck_generator.sync import drive_events as de

    enqueued: list[str] = []
    monkeypatch.setattr(de, "enqueue_mode_a", lambda cid: enqueued.append(cid))

    # drain_mode_a_queue will call pull_changes, which needs an authenticated provider
    # just stub it to avoid Drive API calls
    monkeypatch.setattr(de, "drain_mode_a_queue", lambda **kw: enqueued.copy())

    ret = main([
        "drive", "webhook", "simulate",
        "--channel-id", "chan-sim",
        "--state", "change",
        "--state-db", str(db),
    ])
    assert ret == 0
    assert "chan-sim" in enqueued
