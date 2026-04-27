"""Persistent state record types (schema_versioned)."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
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
class SourceRecord:
    """Source sync metadata.

    ``content_sha256`` for ``provider: local-filesystem`` is the SHA-256 hex digest of **raw file bytes**
    on disk (before ingest), not of extracted/normalized text.
    """

    source_id: str
    provider: str
    external_id: str
    revision_id: str = ""
    etag: str = ""
    content_sha256: str = ""
    last_ingested_at: datetime | None = None
    schema_version: int = 1
    user_id: str = "default"


@dataclass
class ChunkRecord:
    source_id: str
    chunk_index: int
    chunk_sha256: str
    processed_at: datetime | None = None
    model_id: str = ""
    llm_output_card_ids: list[str] = field(default_factory=list)
    schema_version: int = 1
    user_id: str = "default"


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
    ankiweb_note_id: int | None = None
    ankiweb_last_synced_at: datetime | None = None
    ankiweb_last_synced_fields: dict[str, str] | None = None


@dataclass
class DriveChannelRecord:
    channel_id: str
    resource_id: str = ""
    page_token: str = ""
    expiration: datetime | None = None
    schema_version: int = 1
    user_id: str = "default"


@dataclass
class RunReportRecord:
    run_id: str
    trigger: str = "manual"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    sync_report_json: str = "{}"
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
