"""Canonical card persistence models (shared across consumers)."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


def compute_card_content_hash(
    *,
    simplified: str,
    traditional: str,
    pinyin: str,
    meaning: str,
    part_of_speech: str,
    usage_notes: str,
) -> str:
    """Stable SHA-256 over semantic card fields (sentence column excluded)."""
    parts = (
        simplified.strip(),
        traditional.strip(),
        pinyin.strip(),
        meaning.strip(),
        part_of_speech.strip(),
        usage_notes.strip(),
    )
    payload = "\x1e".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class CardUpsertResult(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass
class CardRecord:
    card_id: str
    simplified: str
    traditional: str = ""
    pinyin: str = ""
    meaning: str = ""
    part_of_speech: str = ""
    usage_notes: str = ""
    sentence_simplified: str = ""
    first_seen_source_id: str = ""
    last_updated_at: datetime | None = None
    content_hash: str = ""
    schema_version: int = 1
    user_id: str = "default"


def record_to_jsonable(obj: Any) -> Any:
    """Convert datetimes in nested dicts/lists for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: record_to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [record_to_jsonable(v) for v in obj]
    return obj


def record_asdict_for_roundtrip(obj: Any) -> dict[str, Any]:
    return record_to_jsonable(asdict(obj))
