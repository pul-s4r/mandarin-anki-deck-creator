"""Tests for the Drive webhook receiver (D6)."""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from anki_deck_generator.state.records import DriveChannelRecord
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.web.app import create_app


@pytest.fixture
def client_with_store(tmp_path: Path):
    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()

    # Insert a known channel.
    store.upsert_drive_channel(
        DriveChannelRecord(
            channel_id="chan-test",
            resource_id="res-test",
            page_token="tok-1",
            channel_token="secret-token-abc",
            source_set_name="my-set",
        )
    )

    app = create_app()
    app.state.state_store = store
    return TestClient(app), store


def _post(client: TestClient, headers: dict) -> "Response":
    return client.post("/api/drive/notifications", headers=headers)


# ─────────────────── sync state ─────────────────────────────────────────── #


def test_sync_state_returns_200_no_side_effects(client_with_store) -> None:
    client, store = client_with_store
    r = _post(client, {
        "X-Goog-Channel-ID": "chan-test",
        "X-Goog-Channel-Token": "secret-token-abc",
        "X-Goog-Resource-State": "sync",
    })
    assert r.status_code == 200


# ─────────────────── change state ─────────────────────────────────────────── #


def test_change_state_enqueues_mode_a(client_with_store, monkeypatch: pytest.MonkeyPatch) -> None:
    from anki_deck_generator.sync import drive_events

    enqueued: list[str] = []
    monkeypatch.setattr(drive_events, "enqueue_mode_a", lambda cid: enqueued.append(cid))

    client, _ = client_with_store
    r = _post(client, {
        "X-Goog-Channel-ID": "chan-test",
        "X-Goog-Channel-Token": "secret-token-abc",
        "X-Goog-Resource-State": "change",
    })
    assert r.status_code == 200
    assert "chan-test" in enqueued


def test_update_state_also_enqueues(client_with_store, monkeypatch: pytest.MonkeyPatch) -> None:
    from anki_deck_generator.sync import drive_events

    enqueued: list[str] = []
    monkeypatch.setattr(drive_events, "enqueue_mode_a", lambda cid: enqueued.append(cid))

    client, _ = client_with_store
    r = _post(client, {
        "X-Goog-Channel-ID": "chan-test",
        "X-Goog-Channel-Token": "secret-token-abc",
        "X-Goog-Resource-State": "update",
    })
    assert r.status_code == 200
    assert enqueued


# ─────────────────── remove state ─────────────────────────────────────────── #


def test_remove_state_returns_200(client_with_store) -> None:
    client, _ = client_with_store
    r = _post(client, {
        "X-Goog-Channel-ID": "chan-test",
        "X-Goog-Channel-Token": "secret-token-abc",
        "X-Goog-Resource-State": "remove",
    })
    assert r.status_code == 200


# ─────────────────── token mismatch ─────────────────────────────────────── #


def test_token_mismatch_returns_403(client_with_store) -> None:
    client, _ = client_with_store
    r = _post(client, {
        "X-Goog-Channel-ID": "chan-test",
        "X-Goog-Channel-Token": "WRONG_TOKEN",
        "X-Goog-Resource-State": "change",
    })
    assert r.status_code == 403


# ─────────────────── missing headers ───────────────────────────────────── #


def test_missing_channel_id_returns_400(client_with_store) -> None:
    client, _ = client_with_store
    r = _post(client, {"X-Goog-Resource-State": "change"})
    assert r.status_code == 400


def test_missing_resource_state_returns_400(client_with_store) -> None:
    client, _ = client_with_store
    r = _post(client, {"X-Goog-Channel-ID": "chan-test"})
    assert r.status_code == 400


# ─────────────────── unknown channel ───────────────────────────────────── #


def test_unknown_channel_returns_200_no_error(client_with_store) -> None:
    """Unknown channels should return 200 (not leak info)."""
    client, _ = client_with_store
    r = _post(client, {
        "X-Goog-Channel-ID": "totally-unknown",
        "X-Goog-Channel-Token": "whatever",
        "X-Goog-Resource-State": "change",
    })
    assert r.status_code == 200


# ─────────────────── duplicate notifications ────────────────────────────── #


def test_duplicate_change_notifications_safe(client_with_store, monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate webhook calls should not raise errors."""
    from anki_deck_generator.sync import drive_events

    enqueued: list[str] = []
    monkeypatch.setattr(drive_events, "enqueue_mode_a", lambda cid: enqueued.append(cid))

    client, _ = client_with_store
    headers = {
        "X-Goog-Channel-ID": "chan-test",
        "X-Goog-Channel-Token": "secret-token-abc",
        "X-Goog-Resource-State": "change",
    }
    for _ in range(3):
        r = _post(client, headers)
        assert r.status_code == 200
    assert len(enqueued) == 3  # each call enqueues; idempotency enforced by Mode A upsert
