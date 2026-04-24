from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.dictionary.enrich import (
    LLM_TRANSLATION_SOURCE_NOTE,
    EnrichmentService,
    VocabularyRow,
    append_usage_note,
    is_unknown_translation,
)
from anki_deck_generator.dictionary.index import DictionaryIndex
from anki_deck_generator.dictionary.source import FileLineDictionarySource
from anki_deck_generator.export.sentence_links import SentenceLinkRow
from anki_deck_generator.ingest.router import extract_text_from_bytes, extract_text_from_path
from anki_deck_generator.linking.sentence_assign import choose_winner_key, find_candidate_matches
from anki_deck_generator.linking.term_index import TermIndex, load_term_index_from_prior_csv
from anki_deck_generator.llm.bedrock_chain import (
    build_bedrock_model,
    extract_vocabulary_from_chunk,
    translate_simplified_terms,
)
from anki_deck_generator.llm.schemas import LlmVocabularyItem
from anki_deck_generator.preprocess.blocks import segment_table_blocks
from anki_deck_generator.preprocess.chunk import chunk_text
from anki_deck_generator.preprocess.fingerprints import sha256_utf8
from anki_deck_generator.preprocess.normalize import normalize_unicode, optional_drop_metadata_lines
from anki_deck_generator.preprocess.sentences import extract_dialogue_sentences
from anki_deck_generator.preprocess.tables import parse_table_block

logger = logging.getLogger(__name__)


@dataclass
class PipelineStats:
    block_count: int
    chunk_count: int
    raw_card_count: int
    deduped_card_count: int
    enriched_count: int
    llm_translation_fallback_count: int
    decomposition_fallback_count: int
    sentence_link_count: int


@dataclass
class PipelineResult:
    rows: list[VocabularyRow]
    sentence_links: list[SentenceLinkRow]
    stats: PipelineStats


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


def dedupe_llm_items(cards: list[LlmVocabularyItem]) -> list[LlmVocabularyItem]:
    """Deduplicate LLM vocabulary items by simplified form (longest meaning wins)."""
    return _dedupe_cards(cards)


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


def _suffix_to_format(suffix: str) -> str | None:
    s = suffix.lower()
    if s == ".pdf":
        return "pdf"
    if s in {".md", ".markdown"}:
        return "markdown"
    if s == ".docx":
        return "docx"
    return None


def extract_llm_vocabulary_items(
    text: str,
    settings: Settings,
    *,
    model: object,
    progress_callback: Callable[[str, int, int], None] | None = None,
    should_run_llm: Callable[[int, str], bool] | None = None,
    load_cached_chunk_cards: Callable[[int], list[LlmVocabularyItem]] | None = None,
    on_chunk_processed: Callable[[int, str, list[LlmVocabularyItem]], None] | None = None,
) -> tuple[list[LlmVocabularyItem], int, int, int]:
    """
    Run LLM extraction over segmented/chunked text.

    ``text`` must already be Unicode-normalized and optionally metadata-filtered.

    ``chunk_seq`` counts each LLM-eligible unit in document order (text chunks + table fallbacks).

    When ``should_run_llm`` is None, every chunk is processed (same behavior as a full pipeline run).

    Returns ``(all_cards, total_llm_chunks, chunks_processed, chunks_skipped)``.
    """
    blocks = segment_table_blocks(text)
    text_chunk_lists: list[list[str]] = []
    table_llm_fallbacks = 0
    for block in blocks:
        if block.kind == "table":
            parsed = parse_table_block(block.text)
            needs_fallback = len(parsed.cards) < 2 or len(parsed.unparsed_lines) >= max(3, len(parsed.cards))
            if needs_fallback:
                table_llm_fallbacks += 1
            continue
        text_chunk_lists.append(
            chunk_text(block.text, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
        )

    total_chunks = sum(len(cl) for cl in text_chunk_lists) + table_llm_fallbacks
    chunk_cursor = 0

    def _chunk_llm_progress() -> None:
        nonlocal chunk_cursor
        if not progress_callback or total_chunks <= 0:
            return
        chunk_cursor += 1
        progress_callback("chunk", chunk_cursor, total_chunks)
        progress_callback("llm", chunk_cursor, total_chunks)

    all_cards: list[LlmVocabularyItem] = []
    text_block_idx = 0
    chunk_seq = 0
    chunks_processed = 0
    chunks_skipped = 0

    for b_idx, block in enumerate(blocks):
        if block.kind == "table":
            parsed = parse_table_block(block.text)
            needs_fallback = len(parsed.cards) < 2 or len(parsed.unparsed_lines) >= max(3, len(parsed.cards))
            all_cards.extend(parsed.cards)
            if needs_fallback:
                sha = sha256_utf8(block.text)
                run_llm = should_run_llm is None or should_run_llm(chunk_seq, sha)
                logger.info(
                    "Processing table block %s/%s LLM fallback (%s chars) seq=%s run_llm=%s",
                    b_idx + 1,
                    len(blocks),
                    len(block.text),
                    chunk_seq,
                    run_llm,
                )
                if run_llm:
                    _chunk_llm_progress()
                    items = extract_vocabulary_from_chunk(model, block.text)
                    all_cards.extend(items)
                    chunks_processed += 1
                    if on_chunk_processed is not None:
                        on_chunk_processed(chunk_seq, sha, items)
                else:
                    if load_cached_chunk_cards is None:
                        raise ValueError("load_cached_chunk_cards required when skipping LLM")
                    cached = load_cached_chunk_cards(chunk_seq)
                    all_cards.extend(cached)
                    chunks_skipped += 1
                    if on_chunk_processed is not None:
                        on_chunk_processed(chunk_seq, sha, cached)
                chunk_seq += 1
            continue

        chunks = text_chunk_lists[text_block_idx]
        text_block_idx += 1
        for i, chunk in enumerate(chunks):
            sha = sha256_utf8(chunk)
            run_llm = should_run_llm is None or should_run_llm(chunk_seq, sha)
            logger.info(
                "Processing text block %s/%s chunk %s/%s (%s chars) seq=%s run_llm=%s",
                b_idx + 1,
                len(blocks),
                i + 1,
                len(chunks),
                len(chunk),
                chunk_seq,
                run_llm,
            )
            if run_llm:
                _chunk_llm_progress()
                items = extract_vocabulary_from_chunk(model, chunk)
                all_cards.extend(items)
                chunks_processed += 1
                if on_chunk_processed is not None:
                    on_chunk_processed(chunk_seq, sha, items)
            else:
                if load_cached_chunk_cards is None:
                    raise ValueError("load_cached_chunk_cards required when skipping LLM")
                cached = load_cached_chunk_cards(chunk_seq)
                all_cards.extend(cached)
                chunks_skipped += 1
                if on_chunk_processed is not None:
                    on_chunk_processed(chunk_seq, sha, cached)
            chunk_seq += 1

    return all_cards, total_chunks, chunks_processed, chunks_skipped


def finish_pipeline_after_llm(
    all_cards: list[LlmVocabularyItem],
    text: str,
    settings: Settings,
    *,
    model: object,
    progress_callback: Callable[[str, int, int], None] | None = None,
    total_llm_chunks: int,
) -> PipelineResult:
    """Dedupe, enrich, optional sentence linking — shared by full and incremental runs."""
    blocks = segment_table_blocks(text)
    block_count = len(blocks)

    raw_card_count = len(all_cards)
    deduped = _dedupe_cards(all_cards)
    rows = [_llm_item_to_row(c, k + 1) for k, c in enumerate(deduped)]

    enricher: EnrichmentService | None = None
    enriched_count = 0
    if settings.cedict_path and settings.cedict_path.is_file():
        source = FileLineDictionarySource(settings.cedict_path)
        index = DictionaryIndex.from_source(source)
        enricher = EnrichmentService(
            index,
            force_overwrite=settings.cedict_force_overwrite,
            enable_decomposition_fallback=settings.enable_decomposition_fallback,
        )
        before = [(r.meaning, r.pinyin, r.traditional) for r in rows]
        rows = [enricher.enrich_row(r) for r in rows]
        after = [(r.meaning, r.pinyin, r.traditional) for r in rows]
        enriched_count = sum(1 for b, a in zip(before, after, strict=True) if b != a)
        if progress_callback:
            progress_callback("enrich", 1, 1)
    else:
        logger.warning("No CEDICT path provided or file missing; skipping dictionary enrichment")

    llm_translation_fallback_count = 0
    if settings.enable_llm_translation_fallback:
        missing_terms: list[str] = []
        for r in rows:
            if not is_unknown_translation(r.meaning):
                continue
            t = r.simplified.strip()
            if t:
                missing_terms.append(t)
        uniq_terms = list(dict.fromkeys(missing_terms))
        if uniq_terms:
            if progress_callback:
                progress_callback("llm_translation_fallback", 1, 1)
            try:
                translations = translate_simplified_terms(model, uniq_terms)
            except Exception:
                logger.exception("LLM translation fallback failed")
                translations = {}
            for r in rows:
                if not is_unknown_translation(r.meaning):
                    continue
                t = r.simplified.strip()
                eng = translations.get(t, "").strip()
                if not eng:
                    continue
                r.meaning = eng
                append_usage_note(r, LLM_TRANSLATION_SOURCE_NOTE)
                llm_translation_fallback_count += 1

    decomposition_fallback_count = 0
    if enricher is not None and settings.enable_decomposition_fallback:
        if progress_callback:
            progress_callback("decomposition_fallback", 1, 1)
        for r in rows:
            if not is_unknown_translation(r.meaning):
                continue
            before_m = r.meaning
            enricher.apply_decomposition_to_row(r)
            if r.meaning != before_m and not is_unknown_translation(r.meaning):
                decomposition_fallback_count += 1

    sentence_links: list[SentenceLinkRow] = []
    if settings.enable_sentences:
        if progress_callback:
            progress_callback("sentence_link", 1, 1)
        term_index = TermIndex.from_rows(rows)
        if settings.prior_csv and settings.prior_csv.is_file():
            term_index.merge(load_term_index_from_prior_csv(settings.prior_csv))

        all_terms = term_index.all_terms()
        all_terms.sort(key=len, reverse=True)

        extracted = extract_dialogue_sentences(text)
        by_key: dict[int, list[str]] = {}

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
            sentence_links.append(
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

        max_n = max(0, int(settings.sentences_per_term))
        delim = settings.sentences_delimiter
        if max_n > 0:
            for r in rows:
                sents = by_key.get(int(r.key), [])
                if not sents:
                    continue
                chosen = sents[:max_n]
                r.sentence_simplified = delim.join(chosen)

    if progress_callback:
        progress_callback("export", 1, 1)

    stats = PipelineStats(
        block_count=block_count,
        chunk_count=total_llm_chunks,
        raw_card_count=raw_card_count,
        deduped_card_count=len(deduped),
        enriched_count=enriched_count,
        llm_translation_fallback_count=llm_translation_fallback_count,
        decomposition_fallback_count=decomposition_fallback_count,
        sentence_link_count=len(sentence_links),
    )
    return PipelineResult(rows=rows, sentence_links=sentence_links, stats=stats)


def run_pipeline_from_text(
    text: str,
    settings: Settings,
    *,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> PipelineResult:
    """Core pipeline: normalized note text in → rows (+ optional sentence links) out. No filesystem I/O."""
    text = normalize_unicode(text)
    text = optional_drop_metadata_lines(text, enabled=settings.skip_lines_filter)
    if progress_callback:
        progress_callback("normalize", 1, 1)

    model = build_bedrock_model(settings)

    all_cards, total_chunks, _processed, _skipped = extract_llm_vocabulary_items(
        text,
        settings,
        model=model,
        progress_callback=progress_callback,
    )

    return finish_pipeline_after_llm(
        all_cards,
        text,
        settings,
        model=model,
        progress_callback=progress_callback,
        total_llm_chunks=total_chunks,
    )


def run_pipeline(
    input_path: Path,
    output_csv: Path,
    settings: Settings,
) -> None:
    from anki_deck_generator.export.exporters import SentenceLinksCsvExporter, VocabularyCsvExporter

    fmt = _suffix_to_format(input_path.suffix)
    if fmt is None:
        # Delegate to extract_text_from_path for consistent IngestError message
        text = extract_text_from_path(input_path)
    else:
        text = extract_text_from_bytes(input_path.read_bytes(), format=fmt)
    result = run_pipeline_from_text(text, settings)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_csv.write_bytes(VocabularyCsvExporter(bom=settings.csv_bom).export(result))
    if result.sentence_links:
        sidecar_path = settings.sentence_links_csv or (output_csv.parent / "sentence_links.csv")
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_bytes(SentenceLinksCsvExporter().export(result))
