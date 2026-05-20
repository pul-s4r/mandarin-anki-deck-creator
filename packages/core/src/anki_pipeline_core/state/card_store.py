from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from anki_pipeline_core.models import CardRecord, CardUpsertResult


class CardStore(Protocol):
    """Card read/write contract (canonical term persistence)."""

    def get_card_by_key(self, natural_key: str, *, user_id: str = "default") -> CardRecord | None: ...

    def get_card_by_id(self, card_id: str) -> CardRecord | None: ...

    def upsert_card(self, rec: CardRecord) -> CardUpsertResult: ...

    def iter_cards_changed_since(self, ts: datetime, *, user_id: str = "default") -> Iterable[CardRecord]: ...

    def iter_all_cards(self, *, user_id: str = "default") -> Iterable[CardRecord]: ...
