from __future__ import annotations

import inspect
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from anki_deck_generator.errors import StateError
from anki_deck_generator.state.records import CardRecord, CardUpsertResult, ChunkRecord, RunReportRecord, SourceRecord
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.state.store import StateStore


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    p = tmp_path / "state.db"
    s = SqliteStateStore(p)
    s.init_schema()
    return s


def test_get_source_record_respects_user_id(store: SqliteStateStore) -> None:
    now = datetime.now(UTC)
    store.upsert_source_record(
        SourceRecord(
            source_id="sid-alice",
            provider="local-filesystem",
            external_id="/same/path.md",
            content_sha256="hash_alice",
            last_ingested_at=now,
            user_id="alice",
        )
    )
    store.upsert_source_record(
        SourceRecord(
            source_id="sid-bob",
            provider="local-filesystem",
            external_id="/same/path.md",
            content_sha256="hash_bob",
            last_ingested_at=now,
            user_id="bob",
        )
    )
    ga = store.get_source_record("local-filesystem", "/same/path.md", user_id="alice")
    gb = store.get_source_record("local-filesystem", "/same/path.md", user_id="bob")
    assert ga is not None and ga.content_sha256 == "hash_alice"
    assert gb is not None and gb.content_sha256 == "hash_bob"


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


def test_roundtrip_source_chunk_card_run(store: SqliteStateStore) -> None:
    now = datetime.now(UTC)
    src = SourceRecord(
        source_id="sid1",
        provider="local-filesystem",
        external_id="/tmp/a.md",
        content_sha256="deadbeef",
        last_ingested_at=now,
    )
    store.upsert_source_record(src)
    got = store.get_source_record("local-filesystem", "/tmp/a.md")
    assert got is not None
    assert got.content_sha256 == "deadbeef"

    ch = ChunkRecord(
        source_id="sid1",
        chunk_index=0,
        chunk_sha256="abc",
        processed_at=now,
        model_id="m1",
        llm_output_card_ids=["u1"],
    )
    store.upsert_processed_chunk(ch)
    assert store.get_processed_chunk("sid1", 0) is not None

    cr = CardRecord(
        card_id="u1",
        simplified="词",
        traditional="詞",
        pinyin="cí",
        meaning="word",
        part_of_speech="noun",
        usage_notes="",
        first_seen_source_id="sid1",
        last_updated_at=now,
        content_hash="",
    )
    assert store.upsert_card(cr) is CardUpsertResult.CREATED
    assert store.upsert_card(cr) is CardUpsertResult.UNCHANGED
    cr2 = CardRecord(
        card_id="u1",
        simplified="词",
        traditional="詞",
        pinyin="cí",
        meaning="word2",
        part_of_speech="noun",
        usage_notes="",
        first_seen_source_id="sid1",
        last_updated_at=now,
        content_hash="",
    )
    assert store.upsert_card(cr2) is CardUpsertResult.UPDATED

    store.record_run(
        RunReportRecord(
            run_id="run1",
            trigger="test",
            started_at=now,
            finished_at=now,
            sync_report_json='{"ok": true}',
        )
    )
    runs = list(store.iter_runs())
    assert len(runs) == 1


def test_iter_cards_changed_since(store: SqliteStateStore) -> None:
    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    t1 = datetime(2020, 6, 1, tzinfo=UTC)
    store.upsert_card(
        CardRecord(
            card_id="c1",
            simplified="一",
            meaning="one",
            last_updated_at=t0,
            first_seen_source_id="s",
        )
    )
    store.upsert_card(
        CardRecord(
            card_id="c2",
            simplified="二",
            meaning="two",
            last_updated_at=t1,
            first_seen_source_id="s",
        )
    )
    changed = {c.simplified for c in store.iter_cards_changed_since(datetime(2020, 3, 1, tzinfo=UTC))}
    assert changed == {"二"}


def test_concurrent_upserts(store: SqliteStateStore) -> None:
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            for _ in range(20):
                store.upsert_card(
                    CardRecord(
                        card_id=f"id{i}",
                        simplified=f"w{i}",
                        meaning="x",
                        first_seen_source_id="s",
                    )
                )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(list(store.iter_all_cards())) == 3
