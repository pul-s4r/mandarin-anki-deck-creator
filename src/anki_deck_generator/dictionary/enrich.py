from __future__ import annotations

from dataclasses import dataclass

from anki_deck_generator.dictionary.index import DictionaryIndex
from anki_deck_generator.dictionary.pinyin_normalize import cedict_pinyin_to_tone_marks


def _is_unknown(s: str) -> bool:
    t = (s or "").strip().lower()
    return t in {"", "[unknown]", "unknown", "n/a", "na"}


@dataclass
class VocabularyRow:
    key: int = 0
    simplified: str = ""
    traditional: str = ""
    pinyin: str = ""
    meaning: str = ""
    part_of_speech: str = ""
    usage_notes: str = ""


class EnrichmentService:
    def __init__(self, index: DictionaryIndex, *, force_overwrite: bool = False) -> None:
        self._index = index
        self._force_overwrite = force_overwrite

    def enrich_row(self, row: VocabularyRow) -> VocabularyRow:
        entries = self._index.lookup_headword(row.simplified.strip())
        if not entries:
            return row
        primary = entries[0]
        meaning_join = "; ".join(g.strip() for g in primary.glosses if g.strip())
        pinyin_marked = cedict_pinyin_to_tone_marks(primary.pinyin_raw)

        if self._force_overwrite or _is_unknown(row.meaning) or not row.meaning.strip():
            row.meaning = meaning_join or row.meaning
        if self._force_overwrite or _is_unknown(row.pinyin) or not row.pinyin.strip():
            row.pinyin = pinyin_marked or row.pinyin
        if not row.traditional.strip():
            row.traditional = primary.traditional
        return row
