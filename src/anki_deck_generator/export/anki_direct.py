"""Direct desktop Anki export via AnkiConnect (local delivery mode)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki_deck_generator.export.ankiweb.anki_connect import AnkiConnectClient
from anki_deck_generator.export.ankiweb.exporter import export_to_ankiweb

if TYPE_CHECKING:
    from anki_deck_generator.state.store import StateStore
    from anki_deck_generator.sync.report import AnkiWebExportReport

_DEFAULT_MODEL = "Chinese vocabulary"
_DEFAULT_URL = "http://127.0.0.1:8765"


@dataclass
class VocabularyAnkiDirectExporter:
    """Push vocabulary cards from StateStore to desktop Anki via AnkiConnect."""

    deck_name: str
    model_name: str = _DEFAULT_MODEL
    anki_connect_url: str = _DEFAULT_URL
    anki_connect_api_key: str | None = None
    conflict_policy: str = "prefer-remote"
    auto_create_deck: bool = True
    auto_create_model: bool = True
    auto_sync: bool = True

    def apply(
        self,
        state_store: StateStore,
        *,
        user_id: str = "default",
        run_id: str = "",
    ) -> AnkiWebExportReport:
        """Export all cards for *user_id* to desktop Anki and return a sync report row."""
        from anki_deck_generator.sync.report import AnkiWebExportReport

        cards = list(state_store.iter_all_cards(user_id=user_id))
        with AnkiConnectClient(
            base_url=self.anki_connect_url,
            api_key=self.anki_connect_api_key,
        ) as client:
            result = export_to_ankiweb(
                cards=cards,
                state_store=state_store,
                client=client,
                deck_name=self.deck_name,
                model_name=self.model_name,
                conflict_policy=self.conflict_policy,
                auto_create_deck=self.auto_create_deck,
                auto_create_model=self.auto_create_model,
                auto_sync=self.auto_sync,
                user_id=user_id,
            )
        return AnkiWebExportReport(
            agent_id="local-direct",
            batch_id=run_id,
            created=result.created,
            updated=result.updated,
            unchanged=result.unchanged,
            skipped=result.skipped,
            conflicts=len(result.conflicts),
            errors=len(result.errors),
            sync_requested=result.sync_requested,
            sync_status=result.sync_status,
            duration_ms=0,
            exporter="anki",
        )
