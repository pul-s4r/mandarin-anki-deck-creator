"""Convert between pipeline rows / LLM items and CardRecord."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from anki_deck_generator.dictionary.enrich import VocabularyRow
from anki_deck_generator.llm.schemas import LlmVocabularyItem
from anki_deck_generator.state.records import CardRecord, compute_card_content_hash


def vocabulary_row_to_card_record(
    row: VocabularyRow,
    *,
    source_id: str,
    user_id: str = "default",
    existing: CardRecord | None = None,
) -> CardRecord:
    cid = existing.card_id if existing else str(uuid.uuid4())
    first_seen = (
        existing.first_seen_source_id
        if existing and (existing.first_seen_source_id or "").strip()
        else source_id
    )
    now = datetime.now(UTC)
    h = compute_card_content_hash(
        simplified=row.simplified,
        traditional=row.traditional,
        pinyin=row.pinyin,
        meaning=row.meaning,
        part_of_speech=row.part_of_speech,
        usage_notes=row.usage_notes,
    )
    return CardRecord(
        card_id=cid,
        simplified=row.simplified.strip(),
        traditional=row.traditional.strip(),
        pinyin=row.pinyin.strip(),
        meaning=row.meaning.strip(),
        part_of_speech=row.part_of_speech.strip(),
        usage_notes=row.usage_notes.strip(),
        sentence_simplified=row.sentence_simplified.strip(),
        first_seen_source_id=first_seen,
        last_updated_at=now,
        content_hash=h,
        user_id=user_id,
    )


def card_record_to_llm_item(rec: CardRecord) -> LlmVocabularyItem:
    return LlmVocabularyItem(
        simplified=rec.simplified,
        traditional=rec.traditional,
        pinyin=rec.pinyin,
        meaning=rec.meaning,
        part_of_speech=rec.part_of_speech,
        usage_notes=rec.usage_notes,
    )


def card_records_to_pipeline_rows(cards: list[CardRecord]) -> list[VocabularyRow]:
    """Stable ordering by simplified for reproducible CSV export."""
    sorted_cards = sorted(cards, key=lambda c: c.simplified)
    return [
        VocabularyRow(
            key=i + 1,
            simplified=c.simplified,
            traditional=c.traditional,
            pinyin=c.pinyin,
            meaning=c.meaning,
            part_of_speech=c.part_of_speech,
            usage_notes=c.usage_notes,
            sentence_simplified=c.sentence_simplified,
        )
        for i, c in enumerate(sorted_cards)
    ]
