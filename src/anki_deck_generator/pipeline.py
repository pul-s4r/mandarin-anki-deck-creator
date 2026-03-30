from __future__ import annotations

import logging
from pathlib import Path

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.dictionary.enrich import EnrichmentService, VocabularyRow
from anki_deck_generator.dictionary.index import DictionaryIndex
from anki_deck_generator.dictionary.source import FileLineDictionarySource
from anki_deck_generator.export.csv_writer import write_vocabulary_csv
from anki_deck_generator.ingest.router import extract_text_from_path
from anki_deck_generator.llm.bedrock_chain import build_bedrock_model, extract_vocabulary_from_chunk
from anki_deck_generator.llm.schemas import LlmVocabularyItem
from anki_deck_generator.preprocess.chunk import chunk_text
from anki_deck_generator.preprocess.normalize import normalize_unicode, optional_drop_metadata_lines

logger = logging.getLogger(__name__)


def _dedupe_cards(cards: list[LlmVocabularyItem]) -> list[LlmVocabularyItem]:
    by: dict[str, LlmVocabularyItem] = {}
    for c in cards:
        key = c.simplified.strip()
        if not key:
            continue
        prev = by.get(key)
        if prev is None:
            by[key] = c
        elif len(c.meaning) > len(prev.meaning):
            by[key] = c
    return list(by.values())


def _llm_item_to_row(item: LlmVocabularyItem, key: int) -> VocabularyRow:
    return VocabularyRow(
        key=key,
        simplified=item.simplified.strip(),
        traditional=item.traditional.strip(),
        pinyin=item.pinyin.strip(),
        meaning=item.meaning.strip(),
        part_of_speech=item.part_of_speech.strip(),
        usage_notes=item.usage_notes.strip(),
    )


def run_pipeline(
    input_path: Path,
    output_csv: Path,
    settings: Settings,
) -> None:
    text = extract_text_from_path(input_path)
    text = normalize_unicode(text)
    text = optional_drop_metadata_lines(text, enabled=settings.skip_lines_filter)
    chunks = chunk_text(
        text,
        chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
    )
    model = build_bedrock_model(settings)
    all_cards: list[LlmVocabularyItem] = []
    for i, chunk in enumerate(chunks):
        logger.info("Processing chunk %s/%s (%s chars)", i + 1, len(chunks), len(chunk))
        all_cards.extend(extract_vocabulary_from_chunk(model, chunk))
    deduped = _dedupe_cards(all_cards)
    rows = [_llm_item_to_row(c, k + 1) for k, c in enumerate(deduped)]

    if settings.cedict_path and settings.cedict_path.is_file():
        source = FileLineDictionarySource(settings.cedict_path)
        index = DictionaryIndex.from_source(source)
        enricher = EnrichmentService(
            index,
            force_overwrite=settings.cedict_force_overwrite,
        )
        rows = [enricher.enrich_row(r) for r in rows]
    else:
        logger.warning("No CEDICT path provided or file missing; skipping dictionary enrichment")

    write_vocabulary_csv(output_csv, rows, bom=settings.csv_bom)
