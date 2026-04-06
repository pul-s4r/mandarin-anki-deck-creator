from __future__ import annotations

import logging
from pathlib import Path

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.dictionary.enrich import EnrichmentService, VocabularyRow
from anki_deck_generator.dictionary.index import DictionaryIndex
from anki_deck_generator.dictionary.source import FileLineDictionarySource
from anki_deck_generator.export.csv_writer import write_vocabulary_csv
from anki_deck_generator.export.sentence_links import SentenceLinkRow, write_sentence_links_csv
from anki_deck_generator.ingest.router import extract_text_from_path
from anki_deck_generator.linking.sentence_assign import choose_winner_key, find_candidate_matches
from anki_deck_generator.linking.term_index import TermIndex, load_term_index_from_prior_csv
from anki_deck_generator.llm.bedrock_chain import build_bedrock_model, extract_vocabulary_from_chunk
from anki_deck_generator.llm.schemas import LlmVocabularyItem
from anki_deck_generator.preprocess.blocks import segment_table_blocks
from anki_deck_generator.preprocess.chunk import chunk_text
from anki_deck_generator.preprocess.normalize import normalize_unicode, optional_drop_metadata_lines
from anki_deck_generator.preprocess.sentences import extract_dialogue_sentences
from anki_deck_generator.preprocess.tables import parse_table_block

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
    model = build_bedrock_model(settings)

    blocks = segment_table_blocks(text)
    all_cards: list[LlmVocabularyItem] = []
    for b_idx, block in enumerate(blocks):
        if block.kind == "table":
            parsed = parse_table_block(block.text)
            # LLM fallback when we fail to extract meaningful rows or there is significant noise.
            needs_fallback = len(parsed.cards) < 2 or len(parsed.unparsed_lines) >= max(3, len(parsed.cards))
            all_cards.extend(parsed.cards)
            if needs_fallback:
                logger.info(
                    "Table block %s/%s ambiguous; running LLM fallback (%s lines, %s parsed rows)",
                    b_idx + 1,
                    len(blocks),
                    len(block.text.splitlines()),
                    len(parsed.cards),
                )
                all_cards.extend(extract_vocabulary_from_chunk(model, block.text))
            continue

        chunks = chunk_text(
            block.text,
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )
        for i, chunk in enumerate(chunks):
            logger.info(
                "Processing text block %s/%s chunk %s/%s (%s chars)",
                b_idx + 1,
                len(blocks),
                i + 1,
                len(chunks),
                len(chunk),
            )
            cards = extract_vocabulary_from_chunk(model, chunk)
            all_cards.extend(cards)
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

    if settings.enable_sentences:
        term_index = TermIndex.from_rows(rows)
        if settings.prior_csv and settings.prior_csv.is_file():
            term_index.merge(load_term_index_from_prior_csv(settings.prior_csv))

        all_terms = term_index.all_terms()
        # Best effort: longer terms first tends to reduce match noise.
        all_terms.sort(key=len, reverse=True)

        extracted = extract_dialogue_sentences(text)
        by_key: dict[int, list[str]] = {}
        sidecar_rows: list[SentenceLinkRow] = []

        for s_idx, s in enumerate(extracted, start=1):
            candidates = find_candidate_matches(s.text, all_terms)
            linked_key = choose_winner_key(
                s.text,
                index=term_index,
                candidate_matches=candidates,
                strategy=settings.sentence_assignment_strategy,
                random_seed=settings.sentence_random_seed,
            )
            if linked_key is None:
                continue
            by_key.setdefault(linked_key, []).append(s.text)
            match_debug = ",".join(f"{m.term}@{m.start}" for m in candidates)
            sidecar_rows.append(
                SentenceLinkRow(
                    sentence_id=str(s_idx),
                    sentence_simplified=s.text,
                    sentence_traditional="",
                    sentence_pinyin="",
                    sentence_meaning="",
                    linked_key=linked_key,
                    source=s.source,
                    match_debug=match_debug,
                )
            )

        # Merge into main rows
        max_n = max(0, int(settings.sentences_per_term))
        delim = settings.sentences_delimiter
        if max_n > 0:
            for r in rows:
                sents = by_key.get(int(r.key), [])
                if not sents:
                    continue
                chosen = sents[:max_n]
                r.sentence_simplified = delim.join(chosen)

        # Write sidecar
        sidecar_path = settings.sentence_links_csv or (output_csv.parent / "sentence_links.csv")
        write_sentence_links_csv(sidecar_path, sidecar_rows)

    write_vocabulary_csv(output_csv, rows, bom=settings.csv_bom)
