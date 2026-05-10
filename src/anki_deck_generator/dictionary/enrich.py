from __future__ import annotations

from dataclasses import dataclass

from anki_deck_generator.dictionary.index import DictionaryIndex
from anki_deck_generator.dictionary.parser import CedictEntry
from anki_deck_generator.dictionary.pinyin_normalize import cedict_pinyin_to_tone_marks


def _is_unknown(s: str) -> bool:
    t = (s or "").strip().lower()
    return t in {"", "[unknown]", "unknown", "n/a", "na"}


def is_unknown_translation(s: str) -> bool:
    """True when *s* should be treated as a missing English gloss for enrichment / backfill."""
    return _is_unknown(s)


_DECOMP_SOURCE_NOTE = "(translation source: CEDICT decomposition)"
LLM_TRANSLATION_SOURCE_NOTE = "(translation source: LLM translation fallback)"


def _primary_gloss(entry: CedictEntry) -> str:
    for g in entry.glosses:
        t = (g or "").strip()
        if t:
            return t
    return ""


def _decompose_via_cedict(index: DictionaryIndex, simplified: str) -> list[CedictEntry] | None:
    """
    Greedy longest-match segmentation against CEDICT headwords.

    Returns a list of entries (one per segment) when fully segmentable,
    otherwise None.
    """
    s = (simplified or "").strip()
    if len(s) < 2:
        return None

    out: list[CedictEntry] = []
    i = 0
    max_segments = 12

    while i < len(s):
        if len(out) >= max_segments:
            return None

        found: CedictEntry | None = None
        for j in range(len(s), i, -1):
            sub = s[i:j]
            entries = index.lookup_headword(sub)
            if not entries:
                continue
            found = entries[0]
            break

        if found is None:
            return None

        out.append(found)
        i += len(found.simplified)

    if len(out) <= 1:
        return None
    return out


@dataclass
class VocabularyRow:
    key: int = 0
    simplified: str = ""
    traditional: str = ""
    pinyin: str = ""
    meaning: str = ""
    part_of_speech: str = ""
    usage_notes: str = ""
    sentence_simplified: str = ""
    sentence_traditional: str = ""
    sentence_pinyin: str = ""
    sentence_meaning: str = ""


def _append_usage_source(row: VocabularyRow, note: str) -> None:
    note = (note or "").strip()
    if not note:
        return
    existing = (row.usage_notes or "").strip()
    if note in existing:
        return
    row.usage_notes = f"{existing}; {note}".strip("; ") if existing else note


def append_usage_note(row: VocabularyRow, note: str) -> None:
    _append_usage_source(row, note)


class EnrichmentService:
    def __init__(
        self,
        index: DictionaryIndex,
        *,
        force_overwrite: bool = False,
        enable_decomposition_fallback: bool = True,
    ) -> None:
        self._index = index
        self._force_overwrite = force_overwrite
        self._enable_decomposition_fallback = enable_decomposition_fallback

    def decompose_and_lookup(self, simplified: str) -> list[CedictEntry] | None:
        """Greedy longest-match segmentation against CEDICT headwords, or None if not fully segmentable."""
        return _decompose_via_cedict(self._index, simplified)

    def apply_decomposition_to_row(self, row: VocabularyRow) -> bool:
        """Attempt CEDICT-based decomposition for *row.simplified* and fill fields.

        Returns True if any of (meaning, pinyin, traditional, usage_notes) changed.
        """
        if not self._enable_decomposition_fallback:
            return False
        simplified = row.simplified.strip()
        if not simplified:
            return False
        if self._index.lookup_headword(simplified):
            return False
        decomposed = self.decompose_and_lookup(simplified)
        if not decomposed:
            return False

        meaning_join = " ".join(t for t in (_primary_gloss(e) for e in decomposed) if t)
        pinyin_marked = " ".join(t for t in (cedict_pinyin_to_tone_marks(e.pinyin_raw) for e in decomposed) if t)
        trad = "".join(e.traditional for e in decomposed if e.traditional)

        old_meaning = row.meaning
        old_pinyin = row.pinyin
        old_traditional = row.traditional
        old_usage = row.usage_notes

        if self._force_overwrite or _is_unknown(row.meaning) or not row.meaning.strip():
            row.meaning = meaning_join or row.meaning
        if self._force_overwrite or _is_unknown(row.pinyin) or not row.pinyin.strip():
            row.pinyin = pinyin_marked or row.pinyin
        if not row.traditional.strip():
            row.traditional = trad

        if meaning_join.strip() or pinyin_marked.strip() or trad.strip():
            _append_usage_source(row, _DECOMP_SOURCE_NOTE)

        return (
            (row.meaning or "").strip() != (old_meaning or "").strip()
            or (row.pinyin or "").strip() != (old_pinyin or "").strip()
            or (row.traditional or "").strip() != (old_traditional or "").strip()
            or (row.usage_notes or "").strip() != (old_usage or "").strip()
        )

    def enrich_row(self, row: VocabularyRow) -> VocabularyRow:
        simplified = row.simplified.strip()
        entries = self._index.lookup_headword(simplified)
        if not entries:
            return row
        primary = entries[0]
        meaning_join = "; ".join(g.strip() for g in primary.glosses if g.strip())
        pinyin_marked = cedict_pinyin_to_tone_marks(primary.pinyin_raw)
        trad = primary.traditional

        if self._force_overwrite or _is_unknown(row.meaning) or not row.meaning.strip():
            row.meaning = meaning_join or row.meaning
        if self._force_overwrite or _is_unknown(row.pinyin) or not row.pinyin.strip():
            row.pinyin = pinyin_marked or row.pinyin
        if not row.traditional.strip():
            row.traditional = trad
        return row
