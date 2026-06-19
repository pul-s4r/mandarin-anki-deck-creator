from __future__ import annotations

from pathlib import Path

from anki_deck_generator.dictionary.enrich import VocabularyRow
from anki_deck_generator.pipeline_types import PipelineResult, PipelineStats
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.sync.upload_persistence import persist_api_upload


def test_persist_api_upload_creates_cards_and_run(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()
    try:
        result = PipelineResult(
            rows=[
                VocabularyRow(
                    key=1,
                    simplified="的",
                    traditional="",
                    pinyin="de",
                    meaning="particle",
                    part_of_speech="",
                    usage_notes="",
                    sentence_simplified="",
                )
            ],
            sentence_links=[],
            stats=PipelineStats(
                block_count=1,
                chunk_count=1,
                raw_card_count=1,
                deduped_card_count=1,
                enriched_count=0,
                llm_translation_fallback_count=0,
                decomposition_fallback_count=0,
                sentence_link_count=0,
            ),
        )
        raw = b"1. de - particle\n"
        saved = persist_api_upload(
            store,
            filename="sample.md",
            raw_bytes=raw,
            pipeline_result=result,
        )
        assert saved.cards_created == 1
        assert saved.cards_updated == 0
        assert saved.cards_unchanged == 0

        cards = list(store.iter_all_cards())
        assert len(cards) == 1
        assert cards[0].simplified == "的"

        run = store.get_run(saved.run_id)
        assert run is not None
        assert run.trigger == "api-upload"
    finally:
        store.close()


def test_persist_api_upload_idempotent_reupload(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()
    try:
        row = VocabularyRow(
            key=1,
            simplified="词",
            traditional="",
            pinyin="",
            meaning="word",
            part_of_speech="",
            usage_notes="",
            sentence_simplified="",
        )
        result = PipelineResult(
            rows=[row],
            sentence_links=[],
            stats=PipelineStats(
                block_count=1,
                chunk_count=1,
                raw_card_count=1,
                deduped_card_count=1,
                enriched_count=0,
                llm_translation_fallback_count=0,
                decomposition_fallback_count=0,
                sentence_link_count=0,
            ),
        )
        raw = b"1. ci - word\n"
        first = persist_api_upload(store, filename="sample.md", raw_bytes=raw, pipeline_result=result)
        second = persist_api_upload(store, filename="sample.md", raw_bytes=raw, pipeline_result=result)
        assert first.cards_created == 1
        assert second.cards_created == 0
        assert second.cards_unchanged == 1
        assert len(list(store.iter_all_cards())) == 1
    finally:
        store.close()
