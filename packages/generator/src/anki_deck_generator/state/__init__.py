"""Persistent state (SQLite / future DynamoDB)."""

from __future__ import annotations

from anki_deck_generator.config.settings import Settings, default_state_db_path
from anki_deck_generator.state.records import (
    CardRecord,
    CardUpsertResult,
    ChunkRecord,
    DriveChannelRecord,
    RunReportRecord,
    SourceRecord,
    compute_card_content_hash,
    record_asdict_for_roundtrip,
)
from anki_deck_generator.state.store import StateStore


def get_store(settings: Settings) -> StateStore | None:
    """Return a StateStore when configured; otherwise None (callers use plain pipeline)."""
    if settings.state_backend == "sqlite":
        from anki_deck_generator.state.sqlite_store import SqliteStateStore

        path = settings.state_db_path or default_state_db_path()
        return SqliteStateStore(path)
    return None


__all__ = [
    "CardRecord",
    "CardUpsertResult",
    "ChunkRecord",
    "DriveChannelRecord",
    "RunReportRecord",
    "SourceRecord",
    "StateStore",
    "compute_card_content_hash",
    "get_store",
    "record_asdict_for_roundtrip",
]
