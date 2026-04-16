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


def test_apply_decomposition_fills_compound_headword() -> None:
    entries = [
        CedictEntry(
            traditional="团圆",
            simplified="团圆",
            pinyin_raw="tuan2 yuan2",
            glosses=("reunion",),
        ),
        CedictEntry(
            traditional="饭",
            simplified="饭",
            pinyin_raw="fan4",
            glosses=("meal", "cooked rice"),
        ),
    ]
    index = DictionaryIndex.build(entries)
    svc = EnrichmentService(index)
    row = VocabularyRow(key=1, simplified="团圆饭", traditional="", pinyin="", meaning="")
    changed = svc.apply_decomposition_to_row(row)
    assert changed is True
    assert "reunion" in row.meaning.lower()
    assert "meal" in row.meaning.lower() or "rice" in row.meaning.lower()
    assert "CEDICT decomposition" in row.usage_notes


def test_enrich_decomposition_can_be_disabled() -> None:
    entries = [
        CedictEntry(
            traditional="团圆",
            simplified="团圆",
            pinyin_raw="tuan2 yuan2",
            glosses=("reunion",),
        ),
        CedictEntry(
            traditional="饭",
            simplified="饭",
            pinyin_raw="fan4",
            glosses=("meal",),
        ),
    ]
    index = DictionaryIndex.build(entries)
    svc = EnrichmentService(index, enable_decomposition_fallback=False)
    row = VocabularyRow(key=1, simplified="团圆饭", meaning="", pinyin="")
    changed = svc.apply_decomposition_to_row(row)
    assert changed is False
    assert row.meaning == ""
    assert row.usage_notes == ""


def test_decompose_and_lookup_returns_segments() -> None:
    entries = [
        CedictEntry(
            traditional="团圆",
            simplified="团圆",
            pinyin_raw="tuan2 yuan2",
            glosses=("reunion",),
        ),
        CedictEntry(
            traditional="饭",
            simplified="饭",
            pinyin_raw="fan4",
            glosses=("meal",),
        ),
    ]
    index = DictionaryIndex.build(entries)
    svc = EnrichmentService(index)
    segs = svc.decompose_and_lookup("团圆饭")
    assert segs is not None
    assert [e.simplified for e in segs] == ["团圆", "饭"]
