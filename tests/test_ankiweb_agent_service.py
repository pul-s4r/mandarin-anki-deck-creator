from __future__ import annotations

from datetime import UTC, datetime

import pytest

from anki_deck_generator.state.records import CardRecord, RunReportRecord
from anki_deck_generator.sync.ankiweb_agent_service import (
    AckItemResult,
    ack_batch,
    card_needs_ankiweb_sync,
    decode_cursor,
    encode_cursor,
    issue_pending_batch,
    register_agent,
    select_pending_cards,
)


def test_cursor_roundtrip() -> None:
    now = datetime.now(UTC)
    raw = encode_cursor(now, "card-2")
    got_at, got_id = decode_cursor(raw)
    assert got_at == now
    assert got_id == "card-2"


def test_select_pending_cards_respects_cursor_and_sync_state() -> None:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    t1 = datetime(2024, 2, 1, tzinfo=UTC)
    cards = [
        CardRecord(card_id="a", simplified="甲", last_updated_at=t0, ankiweb_last_synced_at=t0),
        CardRecord(card_id="b", simplified="乙", last_updated_at=t1),
        CardRecord(card_id="c", simplified="丙", last_updated_at=t1, ankiweb_last_synced_at=t0),
    ]
    assert card_needs_ankiweb_sync(cards[0]) is False
    selected = select_pending_cards(cards, cursor_at=t0, cursor_card_id="a", limit=10)
    assert [c.card_id for c in selected] == ["b", "c"]


def test_register_issue_and_ack_roundtrip(tmp_path) -> None:
    from anki_deck_generator.state.sqlite_store import SqliteStateStore

    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()
    now = datetime.now(UTC)
    _, token = register_agent(store, user_id="default", agent_id="desktop")
    assert token

    store.upsert_card(
        CardRecord(
            card_id="c1",
            simplified="词",
            meaning="word",
            last_updated_at=now,
            first_seen_source_id="src",
        )
    )
    store.record_run(
        RunReportRecord(
            run_id="run-1",
            trigger="test",
            started_at=now,
            finished_at=now,
            sync_report_json='{"run_id":"run-1"}',
        )
    )

    batch = issue_pending_batch(
        store,
        user_id="default",
        agent_id="desktop",
        cursor_raw=None,
        limit=50,
        deck_name="D",
        model_name="Chinese vocabulary",
    )
    assert batch.batch_id
    assert len(batch.items) == 1
    assert batch.items[0].op == "create"

    summary = ack_batch(
        store,
        user_id="default",
        agent_id="desktop",
        batch_id=batch.batch_id,
        results=[
            AckItemResult(
                card_id="c1",
                op="create",
                status="applied",
                anki_note_id=123,
                applied_fields={"Meaning": "word", "Simplified": "词"},
            )
        ],
        sync_requested=True,
        sync_status="ok",
        duration_ms=10,
    )
    assert summary.created == 1
    card = store.get_card_by_id("c1")
    assert card is not None and card.ankiweb_note_id == 123

    run = store.get_run("run-1")
    assert run is not None
    assert "ankiweb" in run.sync_report_json

    with pytest.raises(Exception):
        ack_batch(
            store,
            user_id="default",
            agent_id="desktop",
            batch_id=batch.batch_id,
            results=[],
            sync_requested=False,
            sync_status="",
        )
