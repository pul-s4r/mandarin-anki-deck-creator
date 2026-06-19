"""Tests for debounce logic (D7): quiet window, hard deadline, force, in-flight."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from anki_deck_generator.state.records import PendingEditRecord
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.sync.debounce import ReadyReason, is_pending_edit_ready, pending_edit_ready_reason


# ──────────────── unit tests for debounce logic ──────────────────────── #

def _make_rec(
    *,
    ready_at: datetime | None = None,
    hard_deadline_at: datetime | None = None,
    force_process: bool = False,
    last_seen_at: datetime | None = None,
) -> PendingEditRecord:
    now = datetime.now(UTC)
    return PendingEditRecord(
        user_id="default",
        source_set_name="my-set",
        file_id="file-1",
        first_seen_at=now,
        last_seen_at=last_seen_at or now,
        ready_at=ready_at,
        hard_deadline_at=hard_deadline_at,
        force_process=force_process,
    )


def test_not_ready_before_quiet_window() -> None:
    future = datetime.now(UTC) + timedelta(minutes=5)
    rec = _make_rec(ready_at=future, hard_deadline_at=future + timedelta(hours=2))
    assert not is_pending_edit_ready(rec, now=datetime.now(UTC))


def test_ready_after_quiet_window() -> None:
    past = datetime.now(UTC) - timedelta(seconds=1)
    future = datetime.now(UTC) + timedelta(hours=2)
    rec = _make_rec(ready_at=past, hard_deadline_at=future)
    assert is_pending_edit_ready(rec, now=datetime.now(UTC))
    assert pending_edit_ready_reason(rec, now=datetime.now(UTC)) is ReadyReason.QUIET_ELAPSED


def test_ready_at_hard_deadline() -> None:
    future_ready = datetime.now(UTC) + timedelta(hours=1)
    past_deadline = datetime.now(UTC) - timedelta(seconds=1)
    rec = _make_rec(ready_at=future_ready, hard_deadline_at=past_deadline)
    assert is_pending_edit_ready(rec, now=datetime.now(UTC))
    assert pending_edit_ready_reason(rec, now=datetime.now(UTC)) is ReadyReason.HARD_DEADLINE


def test_force_overrides_quiet_window() -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    rec = _make_rec(ready_at=future, hard_deadline_at=future, force_process=True)
    assert is_pending_edit_ready(rec, now=datetime.now(UTC))
    assert pending_edit_ready_reason(rec, now=datetime.now(UTC)) is ReadyReason.FORCE


# ─────────────── integration tests with SQLiteStateStore ──────────────── #


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    db = tmp_path / "state.db"
    s = SqliteStateStore(db)
    s.init_schema()
    return s


def test_upsert_creates_pending_edit(store: SqliteStateStore) -> None:
    now = datetime.now(UTC)
    rec = store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="file-1",
        now=now,
        quiet_seconds=600,
        max_delay_seconds=7200,
    )
    assert rec.file_id == "file-1"
    assert rec.source_set_name == "my-set"
    assert rec.first_seen_at is not None
    assert rec.ready_at is not None
    assert rec.hard_deadline_at is not None
    assert rec.ready_at > now
    assert rec.hard_deadline_at > rec.ready_at


def test_upsert_idempotent_slides_quiet_window(store: SqliteStateStore) -> None:
    t0 = datetime.now(UTC)
    store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="file-1",
        now=t0,
        quiet_seconds=600,
        max_delay_seconds=7200,
    )
    t1 = t0 + timedelta(seconds=120)
    rec2 = store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="file-1",
        now=t1,
        quiet_seconds=600,
        max_delay_seconds=7200,
    )
    # ready_at should be based on t1 (slid forward).
    expected_ready = t1 + timedelta(seconds=600)
    assert rec2.ready_at is not None
    delta = abs((rec2.ready_at - expected_ready).total_seconds())
    assert delta < 2


def test_list_ready_returns_only_ready(store: SqliteStateStore) -> None:
    now = datetime.now(UTC)
    # Not-yet-ready edit.
    store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="future-file",
        now=now,
        quiet_seconds=3600,
        max_delay_seconds=7200,
    )
    # Already-ready edit (quiet window in the past).
    past = now - timedelta(hours=1)
    store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="ready-file",
        now=past,
        quiet_seconds=10,
        max_delay_seconds=7200,
    )
    ready = store.list_ready_pending_edits(user_id="default", now=now)
    file_ids = [r.file_id for r in ready]
    assert "ready-file" in file_ids
    assert "future-file" not in file_ids


def test_clear_pending_edit_guarded_clear(store: SqliteStateStore) -> None:
    now = datetime.now(UTC)
    store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="file-1",
        now=now,
        quiet_seconds=10,
        max_delay_seconds=7200,
    )
    # Guard: last_seen_at == now, if_last_seen_before == now + 1s → should clear.
    cleared = store.clear_pending_edit(
        user_id="default",
        source_set_name="my-set",
        file_id="file-1",
        if_last_seen_before=now + timedelta(seconds=1),
    )
    assert cleared is True
    assert store.get_pending_edit(user_id="default", source_set_name="my-set", file_id="file-1") is None


def test_clear_pending_edit_guard_fails_if_newer(store: SqliteStateStore) -> None:
    t0 = datetime.now(UTC)
    store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="file-1",
        now=t0,
        quiet_seconds=10,
        max_delay_seconds=7200,
    )
    # Simulate new edit arriving: slide last_seen_at forward.
    t1 = t0 + timedelta(seconds=30)
    store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="file-1",
        now=t1,
        quiet_seconds=10,
        max_delay_seconds=7200,
    )
    # Guard with old timestamp: t0 < t1 → guard fails.
    cleared = store.clear_pending_edit(
        user_id="default",
        source_set_name="my-set",
        file_id="file-1",
        if_last_seen_before=t0,
    )
    assert cleared is False
    # Row still exists.
    assert store.get_pending_edit(user_id="default", source_set_name="my-set", file_id="file-1") is not None


def test_force_pending_edit_appears_in_ready_list(store: SqliteStateStore) -> None:
    now = datetime.now(UTC)
    store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="stubborn-file",
        now=now,
        quiet_seconds=3600,
        max_delay_seconds=7200,
    )
    # Before force: not ready.
    ready = store.list_ready_pending_edits(user_id="default", now=now)
    assert not any(r.file_id == "stubborn-file" for r in ready)

    store.force_pending_edit(user_id="default", source_set_name="my-set", file_id="stubborn-file")

    ready2 = store.list_ready_pending_edits(user_id="default", now=now)
    assert any(r.file_id == "stubborn-file" for r in ready2)


def test_get_pending_edit_returns_none_when_absent(store: SqliteStateStore) -> None:
    assert store.get_pending_edit(user_id="default", source_set_name="x", file_id="y") is None


def test_upsert_preserves_hard_deadline_on_slide(store: SqliteStateStore) -> None:
    """hard_deadline_at should not move when edits keep arriving."""
    t0 = datetime.now(UTC)
    rec1 = store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="file-1",
        now=t0,
        quiet_seconds=60,
        max_delay_seconds=300,
    )
    hard1 = rec1.hard_deadline_at

    for i in range(1, 6):
        rec = store.upsert_pending_edit_debounced(
            user_id="default",
            source_set_name="my-set",
            file_id="file-1",
            now=t0 + timedelta(seconds=i * 30),
            quiet_seconds=60,
            max_delay_seconds=300,
        )
    # hard_deadline_at should not have changed from the first write.
    rec_final = store.get_pending_edit(user_id="default", source_set_name="my-set", file_id="file-1")
    assert rec_final is not None
    assert rec_final.hard_deadline_at == hard1
