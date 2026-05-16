"""Tests for AnkiWeb exporter orchestration (stubbed AnkiConnect)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_deck_generator.errors import AnkiConnectError
from anki_deck_generator.export.ankiweb.exporter import (
    ANKI_MODEL_FIELDS,
    export_to_ankiweb,
)
from anki_deck_generator.state.records import CardRecord
from anki_deck_generator.state.sqlite_store import SqliteStateStore


def _mk_card(i: int = 0, **kwargs: object) -> CardRecord:
    defaults: dict[str, object] = {
        "card_id": f"c{i}",
        "simplified": f"词{i}",
        "traditional": "",
        "pinyin": "",
        "meaning": "m",
        "part_of_speech": "",
        "usage_notes": "",
        "first_seen_source_id": "src",
        "last_updated_at": datetime.now(UTC),
        "content_hash": "",
        "user_id": "default",
    }
    defaults.update(kwargs)
    return CardRecord(**defaults)  # type: ignore[arg-type]


def _fields(**vals: str) -> dict[str, dict[str, str]]:
    return {k: {"value": v} for k, v in vals.items()}


def _base_client() -> MagicMock:
    c = MagicMock()
    c.version.return_value = 6
    c.request_permission.return_value = {"permission": "granted"}
    c.deck_names.return_value = ["D"]
    c.model_names.return_value = ["Chinese vocabulary"]
    c.model_field_names.return_value = list(ANKI_MODEL_FIELDS)
    c.sync.return_value = None
    return c


def test_preflight_version_too_low() -> None:
    client = _base_client()
    client.version.return_value = 5
    store = MagicMock()
    with pytest.raises(AnkiConnectError, match=">= 6"):
        export_to_ankiweb(
            cards=[_mk_card()],
            state_store=store,
            client=client,
            deck_name="D",
            model_name="Chinese vocabulary",
        )


def test_preflight_permission_denied() -> None:
    client = _base_client()
    client.request_permission.return_value = {"permission": "denied"}
    store = MagicMock()
    with pytest.raises(AnkiConnectError, match="permission"):
        export_to_ankiweb(
            cards=[_mk_card()],
            state_store=store,
            client=client,
            deck_name="D",
            model_name="Chinese vocabulary",
        )


def test_preflight_model_field_mismatch() -> None:
    client = _base_client()
    client.model_field_names.return_value = ["Front", "Back"]
    store = MagicMock()
    with pytest.raises(AnkiConnectError, match="fields"):
        export_to_ankiweb(
            cards=[_mk_card()],
            state_store=store,
            client=client,
            deck_name="D",
            model_name="Chinese vocabulary",
        )


def test_invalid_conflict_policy_raises() -> None:
    client = _base_client()
    store = MagicMock()
    with pytest.raises(ValueError, match="conflict_policy"):
        export_to_ankiweb(
            cards=[_mk_card()],
            state_store=store,
            client=client,
            deck_name="D",
            model_name="Chinese vocabulary",
            conflict_policy="prefer_remote",
        )


def test_create_new_note_persists_state(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    store = SqliteStateStore(db)
    store.init_schema()
    card = _mk_card()
    client = _base_client()
    client.find_notes.return_value = []
    client.can_add_notes_with_error_detail.return_value = [{"canAdd": True}]
    client.add_notes.return_value = [909090]

    r = export_to_ankiweb(
        cards=[card],
        state_store=store,
        client=client,
        deck_name="D",
        model_name="Chinese vocabulary",
    )
    assert r.created == 1
    assert r.sync_requested is True
    assert r.sync_status == "ok"
    loaded = store.get_card_by_id("c0")
    assert loaded is not None
    assert loaded.ankiweb_note_id == 909090
    assert loaded.ankiweb_last_synced_fields is not None


def test_cached_note_id_short_circuits_find_notes(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    store = SqliteStateStore(db)
    store.init_schema()
    card = _mk_card(ankiweb_note_id=7001)
    store.upsert_card(card)

    client = _base_client()
    client.notes_info.return_value = [
        {
            "noteId": 7001,
            "tags": ["ext_id:c0"],
            "fields": _fields(
                Simplified="词0",
                Meaning="m",
                ExtId="c0",
                Traditional="",
                Pinyin="",
                PartOfSpeech="",
                UsageNotes="",
                SourceRef="src",
            ),
        }
    ]

    r = export_to_ankiweb(
        cards=[store.get_card_by_id("c0")],
        state_store=store,
        client=client,
        deck_name="D",
        model_name="Chinese vocabulary",
    )
    client.find_notes.assert_not_called()
    assert r.unchanged == 1


def test_duplicate_can_add_routes_to_merge(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    store = SqliteStateStore(db)
    store.init_schema()
    card = _mk_card(meaning="new_meaning")
    store.upsert_card(card)

    client = _base_client()
    client.find_notes.side_effect = [[], [424242]]
    client.can_add_notes_with_error_detail.return_value = [
        {"canAdd": False, "error": "cannot create note because it is a duplicate"}
    ]
    client.notes_info.return_value = [
        {
            "noteId": 424242,
            "tags": ["ext_id:c0"],
            "fields": _fields(
                Simplified="词0",
                Meaning="old",
                ExtId="c0",
                Traditional="",
                Pinyin="",
                PartOfSpeech="",
                UsageNotes="",
                SourceRef="src",
            ),
        }
    ]

    r = export_to_ankiweb(
        cards=[store.get_card_by_key("词0")],
        state_store=store,
        client=client,
        deck_name="D",
        model_name="Chinese vocabulary",
    )
    assert r.updated == 1
    client.update_note.assert_called_once()


def test_add_notes_null_triggers_resolve_and_update(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    store = SqliteStateStore(db)
    store.init_schema()
    card = _mk_card()
    store.upsert_card(card)

    client = _base_client()
    client.find_notes.side_effect = [[], [777]]
    client.can_add_notes_with_error_detail.return_value = [{"canAdd": True}]
    client.add_notes.return_value = [None]
    client.notes_info.return_value = [
        {
            "noteId": 777,
            "tags": ["ext_id:c0"],
            "fields": _fields(
                Simplified="词0",
                Meaning="remote_old",
                ExtId="c0",
                Traditional="",
                Pinyin="",
                PartOfSpeech="",
                UsageNotes="",
                SourceRef="src",
            ),
        }
    ]

    r = export_to_ankiweb(
        cards=[store.get_card_by_key("词0")],
        state_store=store,
        client=client,
        deck_name="D",
        model_name="Chinese vocabulary",
    )
    assert r.updated >= 1


def test_merge_prefer_remote_keeps_user_edit(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    store = SqliteStateStore(db)
    store.init_schema()
    base_fields = {k: "" for k in ANKI_MODEL_FIELDS}
    base_fields["Simplified"] = "学"
    base_fields["Meaning"] = "study"
    base_fields["ExtId"] = "c0"
    base_fields["SourceRef"] = "src"
    card = _mk_card(simplified="学", meaning="learn", ankiweb_last_synced_fields=dict(base_fields))
    store.upsert_card(card)

    client = _base_client()
    client.find_notes.return_value = [333]
    remote_fields = dict(base_fields)
    remote_fields["Meaning"] = "user_changed"
    client.notes_info.return_value = [
        {
            "noteId": 333,
            "tags": ["ext_id:c0"],
            "fields": _fields(**{k: remote_fields[k] for k in ANKI_MODEL_FIELDS}),
        }
    ]

    r = export_to_ankiweb(
        cards=[store.get_card_by_key("学")],
        state_store=store,
        client=client,
        deck_name="D",
        model_name="Chinese vocabulary",
        conflict_policy="prefer-remote",
    )
    assert r.updated == 0
    client.update_note.assert_not_called()


def test_tag_and_skip_sets_conflict_tag(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    store = SqliteStateStore(db)
    store.init_schema()
    base_fields = {k: ("学" if k == "Simplified" else ("study" if k == "Meaning" else "")) for k in ANKI_MODEL_FIELDS}
    card = _mk_card(simplified="学", meaning="pipeline", ankiweb_last_synced_fields=dict(base_fields))
    store.upsert_card(card)

    client = _base_client()
    client.find_notes.return_value = [444]
    remote_fields = dict(base_fields)
    remote_fields["Meaning"] = "user"
    client.notes_info.return_value = [
        {
            "noteId": 444,
            "tags": ["ext_id:c0"],
            "fields": _fields(**{k: remote_fields[k] for k in ANKI_MODEL_FIELDS}),
        }
    ]

    r = export_to_ankiweb(
        cards=[store.get_card_by_key("学")],
        state_store=store,
        client=client,
        deck_name="D",
        model_name="Chinese vocabulary",
        conflict_policy="tag-and-skip",
    )
    assert r.skipped == 1
    client.add_tags.assert_called_once()
    client.update_note.assert_not_called()


def test_export_hundred_new_cards(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    store = SqliteStateStore(db)
    store.init_schema()
    cards = [_mk_card(i) for i in range(100)]
    for c in cards:
        store.upsert_card(c)

    client = _base_client()
    client.find_notes.return_value = []
    seq = iter(range(1000, 1000 + 100))

    def _add_batch(notes: list[object]) -> list[int]:
        return [next(seq) for _ in notes]

    client.can_add_notes_with_error_detail.return_value = [{"canAdd": True}]
    client.add_notes.side_effect = _add_batch

    r = export_to_ankiweb(
        cards=list(store.iter_all_cards()),
        state_store=store,
        client=client,
        deck_name="D",
        model_name="Chinese vocabulary",
    )
    assert r.created == 100


def test_user_filter_skips_other_user() -> None:
    client = _base_client()
    client.find_notes.return_value = []
    store = MagicMock()
    c_alice = _mk_card(user_id="alice")
    r = export_to_ankiweb(
        cards=[c_alice],
        state_store=store,
        client=client,
        deck_name="D",
        model_name="Chinese vocabulary",
        user_id="default",
    )
    assert r.created == 0
    store.upsert_card.assert_not_called()
