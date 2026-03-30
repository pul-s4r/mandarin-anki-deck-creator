from __future__ import annotations

from anki_deck_generator.llm.schemas import LlmVocabularyItem
from anki_deck_generator.pipeline import _dedupe_cards


def test_dedupe_keeps_longer_meaning() -> None:
    cards = [
        LlmVocabularyItem(
            simplified="的",
            traditional="",
            pinyin="",
            meaning="a",
            part_of_speech="",
            usage_notes="",
        ),
        LlmVocabularyItem(
            simplified="的",
            traditional="",
            pinyin="",
            meaning="longer english gloss",
            part_of_speech="",
            usage_notes="",
        ),
    ]
    out = _dedupe_cards(cards)
    assert len(out) == 1
    assert out[0].meaning == "longer english gloss"
