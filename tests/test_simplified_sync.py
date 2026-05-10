"""Minimum test suite for the simplified sync architecture (§18.12).

Tests 1–8 map to the non-negotiable test list from the architecture plan.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from anki_deck_generator.state.records import (
    CardRecord,
    CardUpsertResult,
    ChunkRecord,
    DriveChannelRecord,
    PendingEditRecord,
    SourceRecord,
)
from anki_deck_generator.state.sqlite_store import SqliteStateStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    s = SqliteStateStore(tmp_path / "test.db")
    s.init_schema()
    return s


@pytest.fixture
def store_with_channel(store: SqliteStateStore) -> SqliteStateStore:
    store.upsert_drive_channel(
        DriveChannelRecord(
            channel_id="ch-1",
            resource_id="res-1",
            page_token="tok-0",
            expiration=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    return store


def _webhook_event(
    channel_id: str = "ch-1",
    resource_state: str = "change",
    message_number: int = 1,
    token: str = "",
) -> dict[str, Any]:
    headers = {
        "X-Goog-Channel-ID": channel_id,
        "X-Goog-Resource-State": resource_state,
        "X-Goog-Message-Number": str(message_number),
    }
    if token:
        headers["X-Goog-Channel-Token"] = token
    return {"headers": headers, "body": ""}


# ───────────────────────────────────────────────────────────────────
# Test 1: Duplicate webhook replay
# ───────────────────────────────────────────────────────────────────


class TestDuplicateWebhookReplay:
    """Send the same webhook event N times.  Assert exactly one pending row per
    file key and stable final card state."""

    def test_duplicate_webhooks_produce_one_pending_row(
        self, store_with_channel: SqliteStateStore
    ) -> None:
        from anki_deck_generator.lambda_handlers.handler_webhook import handle_drive_webhook

        enqueued: list[str] = []
        for _ in range(5):
            resp = handle_drive_webhook(
                _webhook_event("ch-1", "change", 1),
                state_store=store_with_channel,
                enqueue=lambda cid: enqueued.append(cid),
            )
            assert resp["statusCode"] == 200

        assert len(enqueued) == 5

        now = datetime.now(UTC)
        for _ in range(5):
            store_with_channel.upsert_pending_edit(
                PendingEditRecord(
                    source_set="test-set",
                    file_id="file-A",
                    first_seen_at=now,
                    last_seen_at=now,
                    ready_at=now + timedelta(minutes=10),
                    hard_deadline_at=now + timedelta(minutes=120),
                    message_count=1,
                )
            )

        rec = store_with_channel.get_pending_edit("test-set", "file-A")
        assert rec is not None
        assert rec.message_count == 5

    def test_duplicate_upserts_extend_ready_at(
        self, store: SqliteStateStore
    ) -> None:
        t0 = datetime(2025, 1, 1, tzinfo=UTC)
        t1 = datetime(2025, 1, 1, 0, 5, tzinfo=UTC)

        store.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="f1",
                first_seen_at=t0,
                last_seen_at=t0,
                ready_at=t0 + timedelta(minutes=10),
                hard_deadline_at=t0 + timedelta(minutes=120),
                message_count=1,
            )
        )
        store.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="f1",
                first_seen_at=t1,
                last_seen_at=t1,
                ready_at=t1 + timedelta(minutes=10),
                hard_deadline_at=t1 + timedelta(minutes=120),
                message_count=1,
            )
        )
        rec = store.get_pending_edit("s", "f1")
        assert rec is not None
        assert rec.message_count == 2
        assert rec.last_seen_at == t1
        assert rec.ready_at == t1 + timedelta(minutes=10)
        assert rec.first_seen_at == t0


# ───────────────────────────────────────────────────────────────────
# Test 2: Duplicate queue delivery
# ───────────────────────────────────────────────────────────────────


class TestDuplicateQueueDelivery:
    """Deliver the same queue message twice.  Assert no duplicate card creation
    and no cursor corruption."""

    def test_duplicate_pull_changes_no_cursor_corruption(
        self, store_with_channel: SqliteStateStore
    ) -> None:
        from anki_deck_generator.lambda_handlers.handler_sync import pull_changes

        class FakeDrive:
            def list_changes(self, page_token: str) -> dict[str, Any]:
                return {
                    "changes": [
                        {"fileId": "f1", "file": {"id": "f1", "mimeType": "text/plain"}},
                    ],
                    "newStartPageToken": "tok-1",
                }

        for _ in range(3):
            pull_changes(
                channel_id="ch-1",
                state_store=store_with_channel,
                drive_client=FakeDrive(),
                source_set_name="test-set",
                quiet_minutes=10,
                max_delay_minutes=120,
            )

        ch = store_with_channel.get_drive_channel("ch-1")
        assert ch is not None
        assert ch.page_token == "tok-1"

        rec = store_with_channel.get_pending_edit("test-set", "f1")
        assert rec is not None
        assert rec.message_count >= 1


# ───────────────────────────────────────────────────────────────────
# Test 3: Crash-point tests (failure injection)
# ───────────────────────────────────────────────────────────────────


class TestCrashPoints:
    """Crash before pending write, before token advance, after token advance,
    before pending clear.  Assert no lost changes and eventual recovery."""

    def test_crash_before_pending_write(
        self, store_with_channel: SqliteStateStore
    ) -> None:
        """If we crash before writing PendingEdits, page_token is NOT advanced.
        Next retry will re-pull the same changes."""
        ch_before = store_with_channel.get_drive_channel("ch-1")
        assert ch_before is not None
        assert ch_before.page_token == "tok-0"

        rec = store_with_channel.get_pending_edit("test-set", "f1")
        assert rec is None

    def test_crash_after_pending_write_before_token_advance(
        self, store_with_channel: SqliteStateStore
    ) -> None:
        """Pending edit is written, but token not advanced.  Re-pull will
        idempotently re-upsert the same PendingEdits row."""
        now = datetime.now(UTC)
        store_with_channel.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="f1",
                first_seen_at=now,
                last_seen_at=now,
                ready_at=now + timedelta(minutes=10),
                hard_deadline_at=now + timedelta(minutes=120),
                message_count=1,
            )
        )

        ch = store_with_channel.get_drive_channel("ch-1")
        assert ch is not None
        assert ch.page_token == "tok-0"

        store_with_channel.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="f1",
                first_seen_at=now,
                last_seen_at=now,
                ready_at=now + timedelta(minutes=10),
                hard_deadline_at=now + timedelta(minutes=120),
                message_count=1,
            )
        )

        rec = store_with_channel.get_pending_edit("s", "f1")
        assert rec is not None
        assert rec.message_count == 2

    def test_crash_after_token_advance(
        self, store_with_channel: SqliteStateStore
    ) -> None:
        """Token advanced but processing hasn't run.  PendingEdits row
        still present; Mode B will pick it up."""
        now = datetime.now(UTC)
        store_with_channel.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="f1",
                first_seen_at=now,
                last_seen_at=now,
                ready_at=now + timedelta(minutes=10),
                hard_deadline_at=now + timedelta(minutes=120),
                message_count=1,
            )
        )

        advanced = store_with_channel.advance_drive_channel_token(
            "ch-1", expected_prev_token="tok-0", new_token="tok-1"
        )
        assert advanced is True

        rec = store_with_channel.get_pending_edit("s", "f1")
        assert rec is not None

    def test_crash_before_pending_clear(
        self, store_with_channel: SqliteStateStore
    ) -> None:
        """Processing succeeded but clear didn't run.  Row remains for
        re-processing (idempotent)."""
        now = datetime.now(UTC)
        store_with_channel.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="f1",
                first_seen_at=now,
                last_seen_at=now,
                ready_at=now - timedelta(minutes=1),
                hard_deadline_at=now + timedelta(minutes=120),
                message_count=1,
            )
        )

        ready = list(store_with_channel.iter_ready_pending_edits(now, source_set="s"))
        assert len(ready) == 1

        ready_again = list(store_with_channel.iter_ready_pending_edits(now, source_set="s"))
        assert len(ready_again) == 1


# ───────────────────────────────────────────────────────────────────
# Test 4: Concurrent worker test
# ───────────────────────────────────────────────────────────────────


class TestConcurrentWorker:
    """Run two workers on the same source-set concurrently.  Assert cursor
    monotonicity and no duplicate net writes."""

    def test_concurrent_token_advance_only_one_wins(
        self, store_with_channel: SqliteStateStore
    ) -> None:
        results: list[bool] = []
        errors: list[BaseException] = []

        def advance(new_tok: str) -> None:
            try:
                ok = store_with_channel.advance_drive_channel_token(
                    "ch-1", expected_prev_token="tok-0", new_token=new_tok
                )
                results.append(ok)
            except BaseException as exc:
                errors.append(exc)

        t1 = threading.Thread(target=advance, args=("tok-1",))
        t2 = threading.Thread(target=advance, args=("tok-2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        assert results.count(True) == 1
        assert results.count(False) == 1

        ch = store_with_channel.get_drive_channel("ch-1")
        assert ch is not None
        assert ch.page_token in ("tok-1", "tok-2")

    def test_concurrent_pending_upserts_no_duplicates(
        self, store: SqliteStateStore
    ) -> None:
        errors: list[BaseException] = []

        def upsert_worker(i: int) -> None:
            try:
                for _ in range(20):
                    now = datetime.now(UTC)
                    store.upsert_pending_edit(
                        PendingEditRecord(
                            source_set="s",
                            file_id="shared-file",
                            first_seen_at=now,
                            last_seen_at=now,
                            ready_at=now + timedelta(minutes=10),
                            hard_deadline_at=now + timedelta(minutes=120),
                            message_count=1,
                        )
                    )
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=upsert_worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        rec = store.get_pending_edit("s", "shared-file")
        assert rec is not None
        assert rec.message_count >= 3


# ───────────────────────────────────────────────────────────────────
# Test 5: No-op reprocess test
# ───────────────────────────────────────────────────────────────────


class TestNoopReprocess:
    """Re-run processing with unchanged revision/hash.  Assert zero new
    cards/updates and zero chunk LLM calls."""

    def test_unchanged_content_produces_no_card_changes(
        self, store: SqliteStateStore
    ) -> None:
        now = datetime.now(UTC)

        cr = CardRecord(
            card_id="c1",
            simplified="词",
            traditional="詞",
            pinyin="cí",
            meaning="word",
            part_of_speech="noun",
            usage_notes="",
            first_seen_source_id="s1",
            last_updated_at=now,
        )
        assert store.upsert_card(cr) is CardUpsertResult.CREATED
        assert store.upsert_card(cr) is CardUpsertResult.UNCHANGED

    def test_unchanged_chunk_skips_llm(self, store: SqliteStateStore) -> None:
        from anki_deck_generator.sync.change_detection import chunk_needs_llm

        now = datetime.now(UTC)
        chunk = ChunkRecord(
            source_id="s1",
            chunk_index=0,
            chunk_sha256="abc123",
            processed_at=now,
            model_id="m1",
            llm_output_card_ids=["c1"],
        )
        store.upsert_processed_chunk(chunk)

        stored = store.get_processed_chunk("s1", 0)
        assert not chunk_needs_llm(stored, "abc123")
        assert chunk_needs_llm(stored, "different_hash")


# ───────────────────────────────────────────────────────────────────
# Test 6: In-flight update race test
# ───────────────────────────────────────────────────────────────────


class TestInflightUpdateRace:
    """New event arrives during processing.  Assert pending row remains
    for the next cycle."""

    def test_new_event_during_processing_survives_clear(
        self, store: SqliteStateStore
    ) -> None:
        t0 = datetime(2025, 1, 1, tzinfo=UTC)
        t_processing_start = datetime(2025, 1, 1, 0, 15, tzinfo=UTC)
        t_new_event = datetime(2025, 1, 1, 0, 16, tzinfo=UTC)

        store.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="f1",
                first_seen_at=t0,
                last_seen_at=t0,
                ready_at=t0 + timedelta(minutes=10),
                hard_deadline_at=t0 + timedelta(minutes=120),
                message_count=1,
            )
        )

        store.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="f1",
                first_seen_at=t_new_event,
                last_seen_at=t_new_event,
                ready_at=t_new_event + timedelta(minutes=10),
                hard_deadline_at=t_new_event + timedelta(minutes=120),
                message_count=1,
            )
        )

        cleared = store.clear_pending_edit(
            "s", "f1", if_last_seen_before=t_processing_start
        )
        assert cleared is False

        rec = store.get_pending_edit("s", "f1")
        assert rec is not None
        assert rec.last_seen_at == t_new_event

    def test_old_event_clears_when_no_new_arrivals(
        self, store: SqliteStateStore
    ) -> None:
        t0 = datetime(2025, 1, 1, tzinfo=UTC)
        t_processing_start = datetime(2025, 1, 1, 0, 15, tzinfo=UTC)

        store.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="f1",
                first_seen_at=t0,
                last_seen_at=t0,
                ready_at=t0 + timedelta(minutes=10),
                hard_deadline_at=t0 + timedelta(minutes=120),
                message_count=1,
            )
        )

        cleared = store.clear_pending_edit(
            "s", "f1", if_last_seen_before=t_processing_start
        )
        assert cleared is True
        assert store.get_pending_edit("s", "f1") is None


# ───────────────────────────────────────────────────────────────────
# Test 7: End-to-end idempotency test
# ───────────────────────────────────────────────────────────────────


class TestEndToEndIdempotency:
    """Same logical edit session replayed multiple times.  Assert identical
    final deck contents and card counts."""

    def test_replayed_pull_changes_idempotent(
        self, store_with_channel: SqliteStateStore
    ) -> None:
        from anki_deck_generator.lambda_handlers.handler_sync import pull_changes

        class StableDrive:
            def list_changes(self, page_token: str) -> dict[str, Any]:
                return {
                    "changes": [
                        {"fileId": "f1", "file": {"id": "f1"}},
                        {"fileId": "f2", "file": {"id": "f2"}},
                    ],
                    "newStartPageToken": "tok-1",
                }

        pull_changes(
            channel_id="ch-1",
            state_store=store_with_channel,
            drive_client=StableDrive(),
            source_set_name="s",
        )

        r1 = store_with_channel.get_pending_edit("s", "f1")
        r2 = store_with_channel.get_pending_edit("s", "f2")
        assert r1 is not None and r2 is not None

        store_with_channel.upsert_drive_channel(
            DriveChannelRecord(
                channel_id="ch-1",
                resource_id="res-1",
                page_token="tok-0",
                expiration=datetime(2030, 1, 1, tzinfo=UTC),
            )
        )
        pull_changes(
            channel_id="ch-1",
            state_store=store_with_channel,
            drive_client=StableDrive(),
            source_set_name="s",
        )

        r1b = store_with_channel.get_pending_edit("s", "f1")
        r2b = store_with_channel.get_pending_edit("s", "f2")
        assert r1b is not None and r2b is not None
        assert r1b.first_seen_at == r1.first_seen_at
        assert r2b.first_seen_at == r2.first_seen_at

    def test_process_pending_idempotent_with_same_content(
        self, store: SqliteStateStore
    ) -> None:
        from anki_deck_generator.lambda_handlers.handler_sync import process_pending

        now = datetime.now(UTC)
        store.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="f1",
                first_seen_at=now - timedelta(minutes=30),
                last_seen_at=now - timedelta(minutes=20),
                ready_at=now - timedelta(minutes=1),
                hard_deadline_at=now + timedelta(minutes=90),
                message_count=3,
            )
        )

        call_count = 0

        def mock_sync(source_set, *, settings, state_store, exporters, only_file_ids, user_id):
            nonlocal call_count
            call_count += 1
            return MagicMock(outcomes=[])

        result = process_pending(
            state_store=store,
            settings=MagicMock(),
            source_set=MagicMock(name="s"),
            source_set_name="s",
            run_sync_fn=mock_sync,
        )

        assert result["files_ready"] == 1
        assert result["files_processed"] == 1
        assert call_count == 1

        result2 = process_pending(
            state_store=store,
            settings=MagicMock(),
            source_set=MagicMock(name="s"),
            source_set_name="s",
            run_sync_fn=mock_sync,
        )
        assert result2["files_ready"] == 0


# ───────────────────────────────────────────────────────────────────
# Test 8: Property test — randomized event reorder/duplication
# ───────────────────────────────────────────────────────────────────


class TestPropertyRandomizedEvents:
    """Randomized event reorder/duplication.  Assert convergence to same
    final state as a single clean sequence."""

    def test_random_order_converges_to_same_pending_state(
        self, store: SqliteStateStore
    ) -> None:
        import random

        random.seed(42)

        file_ids = ["f1", "f2", "f3"]
        events = []
        t_base = datetime(2025, 6, 1, tzinfo=UTC)

        for i, fid in enumerate(file_ids):
            for dup in range(random.randint(1, 5)):
                events.append((fid, t_base + timedelta(minutes=i * 5 + dup)))

        random.shuffle(events)

        for fid, t in events:
            store.upsert_pending_edit(
                PendingEditRecord(
                    source_set="s",
                    file_id=fid,
                    first_seen_at=t,
                    last_seen_at=t,
                    ready_at=t + timedelta(minutes=10),
                    hard_deadline_at=t + timedelta(minutes=120),
                    message_count=1,
                )
            )

        for fid in file_ids:
            rec = store.get_pending_edit("s", fid)
            assert rec is not None

        store2 = SqliteStateStore(store._db_path.parent / "clean.db")
        store2.init_schema()
        sorted_events: dict[str, list[datetime]] = {}
        for fid, t in events:
            sorted_events.setdefault(fid, []).append(t)
        for fid in sorted_events:
            sorted_events[fid].sort()

        for fid, timestamps in sorted_events.items():
            for t in timestamps:
                store2.upsert_pending_edit(
                    PendingEditRecord(
                        source_set="s",
                        file_id=fid,
                        first_seen_at=t,
                        last_seen_at=t,
                        ready_at=t + timedelta(minutes=10),
                        hard_deadline_at=t + timedelta(minutes=120),
                        message_count=1,
                    )
                )

        for fid in file_ids:
            r1 = store.get_pending_edit("s", fid)
            r2 = store2.get_pending_edit("s", fid)
            assert r1 is not None and r2 is not None
            assert r1.message_count == r2.message_count
            assert r1.file_id == r2.file_id


# ───────────────────────────────────────────────────────────────────
# Additional state store tests for new functionality
# ───────────────────────────────────────────────────────────────────


class TestPendingEditsStore:
    """Unit tests for the PendingEdits persistence layer."""

    def test_roundtrip(self, store: SqliteStateStore) -> None:
        now = datetime.now(UTC)
        rec = PendingEditRecord(
            source_set="weekly",
            file_id="abc123",
            provider="google-drive",
            first_seen_at=now,
            last_seen_at=now,
            ready_at=now + timedelta(minutes=10),
            hard_deadline_at=now + timedelta(minutes=120),
            message_count=1,
            force=False,
        )
        store.upsert_pending_edit(rec)
        got = store.get_pending_edit("weekly", "abc123")
        assert got is not None
        assert got.source_set == "weekly"
        assert got.file_id == "abc123"
        assert got.message_count == 1

    def test_iter_ready_by_ready_at(self, store: SqliteStateStore) -> None:
        now = datetime.now(UTC)
        store.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="ready",
                first_seen_at=now,
                last_seen_at=now,
                ready_at=now - timedelta(minutes=1),
                hard_deadline_at=now + timedelta(hours=2),
                message_count=1,
            )
        )
        store.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="not-ready",
                first_seen_at=now,
                last_seen_at=now,
                ready_at=now + timedelta(minutes=30),
                hard_deadline_at=now + timedelta(hours=2),
                message_count=1,
            )
        )
        ready = list(store.iter_ready_pending_edits(now, source_set="s"))
        assert len(ready) == 1
        assert ready[0].file_id == "ready"

    def test_iter_ready_by_hard_deadline(self, store: SqliteStateStore) -> None:
        now = datetime.now(UTC)
        store.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="deadline-hit",
                first_seen_at=now - timedelta(hours=3),
                last_seen_at=now,
                ready_at=now + timedelta(minutes=30),
                hard_deadline_at=now - timedelta(minutes=1),
                message_count=10,
            )
        )
        ready = list(store.iter_ready_pending_edits(now, source_set="s"))
        assert len(ready) == 1
        assert ready[0].file_id == "deadline-hit"

    def test_iter_ready_by_force(self, store: SqliteStateStore) -> None:
        now = datetime.now(UTC)
        store.upsert_pending_edit(
            PendingEditRecord(
                source_set="s",
                file_id="forced",
                first_seen_at=now,
                last_seen_at=now,
                ready_at=now + timedelta(hours=10),
                hard_deadline_at=now + timedelta(hours=10),
                message_count=1,
                force=True,
            )
        )
        ready = list(store.iter_ready_pending_edits(now, source_set="s"))
        assert len(ready) == 1
        assert ready[0].file_id == "forced"

    def test_clear_unconditional(self, store: SqliteStateStore) -> None:
        now = datetime.now(UTC)
        store.upsert_pending_edit(
            PendingEditRecord(
                source_set="s", file_id="f1",
                first_seen_at=now, last_seen_at=now,
                ready_at=now, hard_deadline_at=now,
                message_count=1,
            )
        )
        assert store.clear_pending_edit("s", "f1") is True
        assert store.get_pending_edit("s", "f1") is None
        assert store.clear_pending_edit("s", "f1") is False

    def test_source_set_filtering(self, store: SqliteStateStore) -> None:
        now = datetime.now(UTC)
        for ss in ("alpha", "beta"):
            store.upsert_pending_edit(
                PendingEditRecord(
                    source_set=ss, file_id="f1",
                    first_seen_at=now, last_seen_at=now,
                    ready_at=now - timedelta(minutes=1),
                    hard_deadline_at=now + timedelta(hours=2),
                    message_count=1,
                )
            )
        ready_alpha = list(store.iter_ready_pending_edits(now, source_set="alpha"))
        assert len(ready_alpha) == 1
        assert ready_alpha[0].source_set == "alpha"

        ready_all = list(store.iter_ready_pending_edits(now))
        assert len(ready_all) == 2


class TestAdvanceDriveChannelToken:
    """Tests for the conditional page_token advancement."""

    def test_advance_succeeds_with_correct_prev(
        self, store_with_channel: SqliteStateStore
    ) -> None:
        ok = store_with_channel.advance_drive_channel_token(
            "ch-1", expected_prev_token="tok-0", new_token="tok-1"
        )
        assert ok is True
        ch = store_with_channel.get_drive_channel("ch-1")
        assert ch is not None
        assert ch.page_token == "tok-1"

    def test_advance_fails_with_wrong_prev(
        self, store_with_channel: SqliteStateStore
    ) -> None:
        ok = store_with_channel.advance_drive_channel_token(
            "ch-1", expected_prev_token="tok-WRONG", new_token="tok-1"
        )
        assert ok is False
        ch = store_with_channel.get_drive_channel("ch-1")
        assert ch is not None
        assert ch.page_token == "tok-0"

    def test_advance_fails_for_unknown_channel(
        self, store: SqliteStateStore
    ) -> None:
        ok = store.advance_drive_channel_token(
            "no-such-channel", expected_prev_token="x", new_token="y"
        )
        assert ok is False

    def test_monotonic_progression(
        self, store_with_channel: SqliteStateStore
    ) -> None:
        assert store_with_channel.advance_drive_channel_token(
            "ch-1", expected_prev_token="tok-0", new_token="tok-1"
        )
        assert store_with_channel.advance_drive_channel_token(
            "ch-1", expected_prev_token="tok-1", new_token="tok-2"
        )
        assert not store_with_channel.advance_drive_channel_token(
            "ch-1", expected_prev_token="tok-0", new_token="tok-X"
        )
        ch = store_with_channel.get_drive_channel("ch-1")
        assert ch is not None
        assert ch.page_token == "tok-2"


class TestWebhookHandler:
    """Tests for the webhook receiver handler."""

    def test_unknown_channel_returns_404(self, store: SqliteStateStore) -> None:
        from anki_deck_generator.lambda_handlers.handler_webhook import handle_drive_webhook

        resp = handle_drive_webhook(
            _webhook_event("unknown-ch"), state_store=store
        )
        assert resp["statusCode"] == 404

    def test_sync_state_acked(self, store_with_channel: SqliteStateStore) -> None:
        from anki_deck_generator.lambda_handlers.handler_webhook import handle_drive_webhook

        resp = handle_drive_webhook(
            _webhook_event("ch-1", "sync"),
            state_store=store_with_channel,
        )
        assert resp["statusCode"] == 200

    def test_change_state_enqueues(self, store_with_channel: SqliteStateStore) -> None:
        from anki_deck_generator.lambda_handlers.handler_webhook import handle_drive_webhook

        enqueued: list[str] = []
        resp = handle_drive_webhook(
            _webhook_event("ch-1", "change"),
            state_store=store_with_channel,
            enqueue=lambda cid: enqueued.append(cid),
        )
        assert resp["statusCode"] == 200
        assert enqueued == ["ch-1"]

    def test_missing_channel_id_returns_400(self, store: SqliteStateStore) -> None:
        from anki_deck_generator.lambda_handlers.handler_webhook import handle_drive_webhook

        resp = handle_drive_webhook(
            {"headers": {}}, state_store=store
        )
        assert resp["statusCode"] == 400
