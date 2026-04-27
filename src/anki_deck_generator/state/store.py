"""StateStore protocol — persistence abstraction for incremental sync."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from anki_deck_generator.state.records import (
        CardRecord,
        CardUpsertResult,
        ChunkRecord,
        DriveChannelRecord,
        RunReportRecord,
        SourceRecord,
    )


@runtime_checkable
class StateStore(Protocol):
    def get_source_record(self, provider: str, external_id: str, *, user_id: str = "default") -> SourceRecord | None: ...

    def upsert_source_record(self, rec: SourceRecord) -> None: ...

    def get_processed_chunk(self, source_id: str, chunk_index: int) -> ChunkRecord | None: ...

    def upsert_processed_chunk(self, rec: ChunkRecord) -> None: ...

    def get_card_by_key(self, natural_key: str, *, user_id: str = "default") -> CardRecord | None: ...

    def get_card_by_id(self, card_id: str) -> CardRecord | None: ...

    def upsert_card(self, rec: CardRecord) -> CardUpsertResult: ...

    def iter_cards_changed_since(self, ts: datetime, *, user_id: str = "default") -> Iterable[CardRecord]: ...

    def iter_all_cards(self, *, user_id: str = "default") -> Iterable[CardRecord]: ...

    def get_drive_channel(self, channel_id: str) -> DriveChannelRecord | None: ...

    def upsert_drive_channel(self, rec: DriveChannelRecord) -> None: ...

    def record_run(self, rec: RunReportRecord) -> None: ...

    def iter_runs(self, *, limit: int = 100) -> Iterable[RunReportRecord]: ...
