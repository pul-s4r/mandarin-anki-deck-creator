"""Pipeline result types shared by pipeline and export layers."""

from __future__ import annotations

from dataclasses import dataclass

from anki_deck_generator.dictionary.enrich import VocabularyRow
from anki_deck_generator.export.sentence_links import SentenceLinkRow


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
