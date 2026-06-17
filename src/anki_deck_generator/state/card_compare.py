"""Shared card record comparison helpers for StateStore backends."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from anki_deck_generator.state.records import CardRecord


def dt_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def normalize_stored_anki_fields(raw: str | dict[str, str] | None) -> dict[str, str]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k): str(v) for k, v in loaded.items()}


def ankiweb_meta_matches_stored(
    *,
    stored_note_id: int | None,
    stored_synced_at: datetime | None,
    stored_synced_fields: str | dict[str, str] | None,
    rec: CardRecord,
) -> bool:
    """True when incoming CardRecord ankiweb metadata matches stored values."""
    rid = rec.ankiweb_note_id
    if stored_note_id is None and rid is None:
        note_ok = True
    elif stored_note_id is not None and rid is not None:
        note_ok = int(stored_note_id) == int(rid)
    else:
        note_ok = False
    if not note_ok:
        return False
    if dt_iso(stored_synced_at) != dt_iso(rec.ankiweb_last_synced_at):
        return False
    return normalize_stored_anki_fields(stored_synced_fields) == dict(rec.ankiweb_last_synced_fields or {})
