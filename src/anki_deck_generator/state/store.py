"""StateStore protocol — persistence abstraction for incremental sync."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from anki_deck_generator.state.records import (
        CardRecord,
        CardUpsertResult,
        ChunkRecord,
        DriveChannelRecord,
        PendingEditRecord,
        RunReportRecord,
        SourceRecord,
    )


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

    def advance_drive_channel_token(
        self,
        channel_id: str,
        *,
        expected_prev_token: str,
        new_token: str,
    ) -> bool:
        """Atomically advance ``page_token`` only if it still equals *expected_prev_token*.

        Returns True on success, False if the condition fails (another writer raced).
        This is the *only* sanctioned way to move the cursor forward.
        """
        ...

    def upsert_pending_edit(self, rec: PendingEditRecord) -> None:
        """Insert or extend a pending-edit row keyed on ``(source_set, file_id)``.

        On conflict the row's ``last_seen_at``, ``ready_at``, and ``message_count``
        are updated; ``first_seen_at`` and ``hard_deadline_at`` are preserved from
        the original insert.
        """
        ...

    def get_pending_edit(self, source_set: str, file_id: str) -> PendingEditRecord | None: ...

    def iter_ready_pending_edits(self, as_of: datetime, *, source_set: str | None = None) -> Iterable[PendingEditRecord]:
        """Return pending edits whose ``ready_at <= as_of`` or ``hard_deadline_at <= as_of`` or ``force``."""
        ...

    def clear_pending_edit(self, source_set: str, file_id: str, *, if_last_seen_before: datetime | None = None) -> bool:
        """Delete a pending-edit row.  If *if_last_seen_before* is given, only delete when
        ``last_seen_at <= if_last_seen_before`` (race-safe: new events that arrived during
        processing keep the row alive).  Returns True if a row was actually deleted.
        """
        ...

    def record_run(self, rec: RunReportRecord) -> None: ...

    def iter_runs(self, *, limit: int = 100) -> Iterable[RunReportRecord]: ...
