from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VocabularyTerm:
    """Canonical vocabulary term (stable term_id for persistence and review)."""

    term_id: str
    simplified: str = ""
    traditional: str = ""
    pinyin: str = ""
    meaning: str = ""
    part_of_speech: str = ""
    usage_notes: str = ""
    sentence_simplified: str = ""
