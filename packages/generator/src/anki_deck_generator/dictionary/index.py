from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from anki_deck_generator.dictionary.parser import CedictEntry, CedictParser
from anki_deck_generator.dictionary.source import DictionarySource


class DictionaryIndex:
    def __init__(self, by_simplified: dict[str, list[CedictEntry]]) -> None:
        self._by_simplified = by_simplified

    @classmethod
    def build(cls, entries: Iterable[CedictEntry]) -> DictionaryIndex:
        by: dict[str, list[CedictEntry]] = defaultdict(list)
        for e in entries:
            by[e.simplified].append(e)
        return cls(dict(by))

    @classmethod
    def from_source(cls, source: DictionarySource, *, parser: CedictParser | None = None) -> DictionaryIndex:
        p = parser or CedictParser()
        parsed: list[CedictEntry] = []
        for line in source.iter_lines():
            row = p.parse_line(line)
            if row:
                parsed.append(row)
        return cls.build(parsed)

    def lookup_headword(self, simplified: str) -> list[CedictEntry]:
        return list(self._by_simplified.get(simplified, ()))
