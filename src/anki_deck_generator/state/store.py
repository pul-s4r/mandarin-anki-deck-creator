"""StateStore protocol — persistence abstraction for incremental sync."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from anki_deck_generator.state.records import (
        AgentRecord,
        CardRecord,
        CardUpsertResult,
        ChunkRecord,
        DriveChannelRecord,
        IssuedBatchRecord,
        PendingSyncCursor,
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
        expected_token: str,
        new_token: str,
    ) -> None: ...

    def record_run(self, rec: RunReportRecord) -> None: ...

    def iter_runs(self, *, limit: int = 100) -> Iterable[RunReportRecord]: ...

    def get_run(self, run_id: str, *, user_id: str = "default") -> RunReportRecord | None: ...

    def update_run_report(self, run_id: str, sync_report_json: str, *, user_id: str = "default") -> None: ...

    def upsert_agent(self, rec: AgentRecord) -> None: ...

    def get_agent(self, agent_id: str, *, user_id: str = "default") -> AgentRecord | None: ...

    def iter_agents(self, *, user_id: str = "default") -> Iterable[AgentRecord]: ...

    def revoke_agent(self, agent_id: str, *, user_id: str = "default") -> None: ...

    def touch_agent_poll(
        self,
        agent_id: str,
        *,
        user_id: str = "default",
        batch_id: str = "",
        sync_status: str = "",
        seen_at: datetime | None = None,
    ) -> None: ...

    def get_agent_cursor(self, agent_id: str, *, user_id: str = "default") -> PendingSyncCursor | None: ...

    def set_agent_cursor(self, rec: PendingSyncCursor) -> None: ...

    def put_issued_batch(self, rec: IssuedBatchRecord) -> None: ...

    def get_issued_batch(self, batch_id: str) -> IssuedBatchRecord | None: ...

    def get_open_batch_for_agent(self, agent_id: str, *, user_id: str = "default") -> IssuedBatchRecord | None: ...

    def mark_batch_acked(self, batch_id: str, *, acked_at: datetime) -> None: ...
