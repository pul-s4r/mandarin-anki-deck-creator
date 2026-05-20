"""Shared LLM-eligible unit enumeration for pipeline extraction and chunk persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.preprocess.blocks import segment_table_blocks
from anki_deck_generator.preprocess.chunk import chunk_text
from anki_deck_generator.preprocess.fingerprints import sha256_utf8
from anki_deck_generator.preprocess.tables import parse_table_block


@dataclass(frozen=True)
class LlmTextUnit:
    """One LLM-eligible text unit in document order (``chunk_seq`` is the index in the returned list)."""

    text: str
    chunk_sha256: str
    kind: Literal["text_chunk", "table_fallback"]


def list_llm_text_units(text: str, settings: Settings) -> list[LlmTextUnit]:
    """
    Enumerate LLM units in the same order as ``extract_llm_vocabulary_items``.

    ``text`` must already be Unicode-normalized and optionally metadata-filtered.
    """
    blocks = segment_table_blocks(text)
    text_chunk_lists: list[list[str]] = []
    for block in blocks:
        if block.kind == "table":
            continue
        text_chunk_lists.append(
            chunk_text(block.text, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
        )

    out: list[LlmTextUnit] = []
    t_idx = 0
    for block in blocks:
        if block.kind == "table":
            parsed = parse_table_block(block.text)
            needs_fallback = len(parsed.cards) < 2 or len(parsed.unparsed_lines) >= max(3, len(parsed.cards))
            if needs_fallback:
                out.append(
                    LlmTextUnit(
                        text=block.text,
                        chunk_sha256=sha256_utf8(block.text),
                        kind="table_fallback",
                    )
                )
            continue
        chunks = text_chunk_lists[t_idx]
        t_idx += 1
        for chunk in chunks:
            out.append(
                LlmTextUnit(
                    text=chunk,
                    chunk_sha256=sha256_utf8(chunk),
                    kind="text_chunk",
                )
            )
    return out
