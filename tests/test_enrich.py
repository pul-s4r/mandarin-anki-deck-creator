from __future__ import annotations

from anki_deck_generator.dictionary.enrich import EnrichmentService, VocabularyRow
from anki_deck_generator.dictionary.index import DictionaryIndex
from anki_deck_generator.dictionary.parser import CedictEntry


def test_enrich_fills_missing_meaning_and_pinyin() -> None:
    entries = [
        CedictEntry(
            traditional="的",
            simplified="的",
            pinyin_raw="de5",
            glosses=("possessive particle",),
        )
    ]
    index = DictionaryIndex.build(entries)
    svc = EnrichmentService(index)
    row = VocabularyRow(key=1, simplified="的", traditional="", pinyin="", meaning="")
    out = svc.enrich_row(row)
    assert "possessive" in out.meaning
    assert out.pinyin == "de"
    assert out.traditional == "的"


def test_enrich_respects_existing_llm_fields() -> None:
    entries = [
        CedictEntry(
            traditional="的",
            simplified="的",
            pinyin_raw="de5",
            glosses=("from dict",),
        )
    ]
    index = DictionaryIndex.build(entries)
    svc = EnrichmentService(index, force_overwrite=False)
    row = VocabularyRow(
        key=1,
        simplified="的",
        traditional="",
        pinyin="dè",
        meaning="already filled",
    )
    out = svc.enrich_row(row)
    assert out.meaning == "already filled"
    assert out.pinyin == "dè"
