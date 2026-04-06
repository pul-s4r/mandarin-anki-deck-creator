from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TermEntry:
    simplified: str
    key: int


class TermIndex:
    def __init__(self) -> None:
        self._by_simplified: dict[str, list[int]] = {}
        self._freq: dict[str, int] = {}

    def add(self, simplified: str, key: int) -> None:
        s = (simplified or "").strip()
        if not s:
            return
        self._by_simplified.setdefault(s, []).append(int(key))
        self._freq[s] = self._freq.get(s, 0) + 1

    def keys_for(self, simplified: str) -> list[int]:
        return list(self._by_simplified.get((simplified or "").strip(), []))

    def frequency(self, simplified: str) -> int:
        return self._freq.get((simplified or "").strip(), 0)

    def all_terms(self) -> list[str]:
        return list(self._by_simplified.keys())

    @classmethod
    def from_rows(cls, rows: list[object]) -> TermIndex:
        """
        Build from VocabularyRow-like objects that have .key and .simplified.
        """
        idx = cls()
        for r in rows:
            key = getattr(r, "key", None)
            simplified = getattr(r, "simplified", "")
            if key is None:
                continue
            idx.add(str(simplified), int(key))
        return idx

    def merge(self, other: TermIndex) -> None:
        for term, keys in other._by_simplified.items():
            for k in keys:
                self.add(term, k)


def load_term_index_from_prior_csv(path: Path) -> TermIndex:
    """
    Load terms from a prior exported vocabulary CSV.

    Expected headers: Key, Simplified (case-sensitive as written by our exporter).
    Extra columns are ignored.
    """
    idx = TermIndex()
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            s = (row.get("Simplified") or "").strip()
            k_raw = (row.get("Key") or "").strip()
            if not s or not k_raw:
                continue
            try:
                k = int(k_raw)
            except ValueError:
                continue
            idx.add(s, k)
    return idx

