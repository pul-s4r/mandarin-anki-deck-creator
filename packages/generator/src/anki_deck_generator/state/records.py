"""Persistent state record types (schema_versioned)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from anki_pipeline_core.models import (
    CardRecord as CoreCardRecord,
    CardUpsertResult,
    compute_card_content_hash,
    record_asdict_for_roundtrip,
    record_to_jsonable,
)

__all__ = [
    "CardRecord",
    "CardUpsertResult",
    "ChunkRecord",
    "DriveChannelRecord",
    "RunReportRecord",
    "SourceRecord",
    "compute_card_content_hash",
    "record_asdict_for_roundtrip",
    "record_to_jsonable",
]


@dataclass
class CardRecord(CoreCardRecord):
    """Generator card row including AnkiWeb sync metadata."""

    ankiweb_note_id: int | None = None
    ankiweb_last_synced_at: datetime | None = None
    ankiweb_last_synced_fields: dict[str, str] | None = None


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
