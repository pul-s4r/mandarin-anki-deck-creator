from __future__ import annotations

import inspect
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from anki_deck_generator.errors import StateError
from anki_deck_generator.state.records import CardRecord, CardUpsertResult
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.state.store import StateStore
from tests.conformance_state_store import StateStoreConformanceTests


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    p = tmp_path / "state.db"
    s = SqliteStateStore(p)
    s.init_schema()
    return s


class TestSqliteStateStoreConformance(StateStoreConformanceTests):
    pass


def test_get_source_record_signature_matches_protocol() -> None:
    proto_sig = inspect.signature(StateStore.get_source_record)
    sqlite_sig = inspect.signature(SqliteStateStore.get_source_record)
    assert proto_sig == sqlite_sig
    assert "user_id" in proto_sig.parameters


def test_write_wraps_sqlite_errors(store: SqliteStateStore) -> None:
    def bad_sql(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT INTO not_a_real_table VALUES (1)")

    with pytest.raises(StateError):
        store._write(bad_sql)


def test_write_propagates_programmer_errors(store: SqliteStateStore) -> None:
    def boom(conn: sqlite3.Connection) -> None:
        raise RuntimeError("not sqlite")

    with pytest.raises(RuntimeError, match="not sqlite"):
        store._write(boom)


def test_migration_adds_ankiweb_columns_to_legacy_db(tmp_path: Path) -> None:
    """Pre-M4 databases had cards without ankiweb_* columns; opening migrates in place."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE cards (
            card_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'default',
            simplified TEXT NOT NULL,
            traditional TEXT NOT NULL DEFAULT '',
            pinyin TEXT NOT NULL DEFAULT '',
            meaning TEXT NOT NULL DEFAULT '',
            part_of_speech TEXT NOT NULL DEFAULT '',
            usage_notes TEXT NOT NULL DEFAULT '',
            sentence_simplified TEXT NOT NULL DEFAULT '',
            first_seen_source_id TEXT NOT NULL DEFAULT '',
            last_updated_at TEXT,
            content_hash TEXT NOT NULL DEFAULT '',
            schema_version INTEGER NOT NULL DEFAULT 1,
            UNIQUE(user_id, simplified)
        );
        """
    )
    conn.commit()
    conn.close()

    store = SqliteStateStore(db)
    store.init_schema()

    with sqlite3.connect(str(db)) as c2:
        cols = {row[1] for row in c2.execute("PRAGMA table_info(cards)")}
    assert "ankiweb_note_id" in cols
    assert "ankiweb_last_synced_at" in cols
    assert "ankiweb_last_synced_fields" in cols

    now = datetime.now(UTC)
    cr = CardRecord(
        card_id="c1",
        simplified="词",
        meaning="same",
        last_updated_at=now,
        first_seen_source_id="s",
        content_hash="",
    )
    assert store.upsert_card(cr) is CardUpsertResult.CREATED
    synced = replace(
        cr,
        ankiweb_note_id=99,
        ankiweb_last_synced_at=now,
        ankiweb_last_synced_fields={"Meaning": "same"},
    )
    assert store.upsert_card(synced) is CardUpsertResult.UPDATED
    got = store.get_card_by_key("词")
    assert got is not None and got.ankiweb_note_id == 99


def test_advance_drive_channel_token_sqlite(store: SqliteStateStore) -> None:
    from anki_deck_generator.state.records import DriveChannelRecord

    store.upsert_drive_channel(
        DriveChannelRecord(channel_id="ch1", page_token="tok-a", resource_id="res1")
    )
    store.advance_drive_channel_token("ch1", expected_token="tok-a", new_token="tok-b")
    got = store.get_drive_channel("ch1")
    assert got is not None and got.page_token == "tok-b"

    with pytest.raises(StateError):
        store.advance_drive_channel_token("ch1", expected_token="tok-a", new_token="tok-c")


# ─────────────── M8: DriveChannelRecord extended fields ─────────────── #


def test_drive_channel_m8_fields_roundtrip(store: SqliteStateStore) -> None:
    from datetime import UTC, datetime

    from anki_deck_generator.state.records import DriveChannelRecord

    now = datetime.now(UTC)
    rec = DriveChannelRecord(
        channel_id="m8-chan",
        resource_id="res-m8",
        page_token="tok-m8",
        source_set_name="set-a",
        channel_token="my-secret-token",
        last_advanced_at=now,
    )
    store.upsert_drive_channel(rec)
    got = store.get_drive_channel("m8-chan")
    assert got is not None
    assert got.source_set_name == "set-a"
    assert got.channel_token == "my-secret-token"
    assert got.last_advanced_at is not None


def test_advance_token_updates_last_advanced_at(store: SqliteStateStore) -> None:
    from datetime import UTC, datetime

    from anki_deck_generator.state.records import DriveChannelRecord

    store.upsert_drive_channel(
        DriveChannelRecord(channel_id="ch-adv", page_token="tok-1", resource_id="r")
    )
    before = datetime.now(UTC)
    store.advance_drive_channel_token("ch-adv", expected_token="tok-1", new_token="tok-2")
    got = store.get_drive_channel("ch-adv")
    assert got is not None
    assert got.last_advanced_at is not None
    assert got.last_advanced_at >= before


def test_list_drive_channels(store: SqliteStateStore) -> None:
    from anki_deck_generator.state.records import DriveChannelRecord

    for i in range(3):
        store.upsert_drive_channel(
            DriveChannelRecord(
                channel_id=f"chan-{i}",
                resource_id=f"res-{i}",
                page_token=f"tok-{i}",
                source_set_name="my-set",
            )
        )
    channels = store.list_drive_channels(user_id="default")
    assert len(channels) == 3


def test_migration_adds_m8_columns_to_legacy_drive_channels(tmp_path: Path) -> None:
    """Pre-M8 drive_channels table without new columns should be migrated."""
    import sqlite3

    db = tmp_path / "legacy_m8.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE drive_channels (
            channel_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'default',
            resource_id TEXT NOT NULL DEFAULT '',
            page_token TEXT NOT NULL DEFAULT '',
            expiration TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.commit()
    conn.close()

    store = SqliteStateStore(db)
    store.init_schema()

    with sqlite3.connect(str(db)) as c2:
        cols = {row[1] for row in c2.execute("PRAGMA table_info(drive_channels)")}
    assert "source_set_name" in cols
    assert "channel_token" in cols
    assert "last_advanced_at" in cols


# ─────────────── M8: PendingEditRecord ──────────────────────────────── #


def test_pending_edit_full_lifecycle(store: SqliteStateStore) -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    # Insert.
    rec = store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="set-a",
        file_id="fid-1",
        now=now,
        quiet_seconds=600,
        max_delay_seconds=7200,
    )
    assert rec.file_id == "fid-1"

    # Get.
    got = store.get_pending_edit(user_id="default", source_set_name="set-a", file_id="fid-1")
    assert got is not None

    # Force.
    store.force_pending_edit(user_id="default", source_set_name="set-a", file_id="fid-1")
    forced = store.get_pending_edit(user_id="default", source_set_name="set-a", file_id="fid-1")
    assert forced is not None and forced.force_process is True

    # Clear.
    cleared = store.clear_pending_edit(
        user_id="default",
        source_set_name="set-a",
        file_id="fid-1",
        if_last_seen_before=now + timedelta(hours=1),
    )
    assert cleared is True
    assert store.get_pending_edit(user_id="default", source_set_name="set-a", file_id="fid-1") is None
