from __future__ import annotations

import re
from dataclasses import dataclass

from anki_deck_generator.llm.schemas import LlmVocabularyItem

_RE_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
_RE_PUNCT_ONLY = re.compile(r"^[\s\W]+$", re.UNICODE)
_RE_PINYINISH = re.compile(
    r"^[A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüÜ0-9\s'\-·]+$"
)


@dataclass(frozen=True)
class TableParseResult:
    cards: list[LlmVocabularyItem]
    unparsed_lines: list[str]


def parse_table_block(block_text: str) -> TableParseResult:
    """
    Parse a tab-separated (or semi-structured) vocabulary table into LlmVocabularyItem rows.

    Rules:
    - Primary split is on literal '\\t'.
    - Accept rows where the term cell contains CJK.
    - Continuation lines (no tabs) may be appended to the previous row's meaning, or used to fill
      the previous row's pinyin when it is empty and the line looks "pinyin-ish".
    """

    cards: list[LlmVocabularyItem] = []
    unparsed: list[str] = []

    def last() -> LlmVocabularyItem | None:
        return cards[-1] if cards else None

    for raw in block_text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip() or _RE_PUNCT_ONLY.match(line):
            continue

        if "\t" not in line:
            prev = last()
            if prev is None:
                unparsed.append(line)
                continue
            if not prev.pinyin.strip() and _RE_PINYINISH.match(line.strip()):
                prev.pinyin = (prev.pinyin + " " + line.strip()).strip()
                continue
            prev.meaning = (prev.meaning + " " + line.strip()).strip()
            continue

        # Preserve empty and whitespace-only cells to avoid shifting columns.
        cells = [c.strip() for c in line.split("\t")]
        if not cells:
            continue

        term = cells[0].strip()
        if not term or not _RE_HAS_CJK.search(term):
            unparsed.append(line)
            continue

        pinyin = cells[1].strip() if len(cells) >= 2 else ""
        meaning = cells[2].strip() if len(cells) >= 3 else ""
        # If there are more than 3 cells, treat the remainder as meaning continuation.
        if len(cells) > 3:
            extra = " ".join(c.strip() for c in cells[3:] if c.strip())
            meaning = (meaning + " " + extra).strip()

        cards.append(
            LlmVocabularyItem(
                simplified=term,
                traditional="",
                pinyin=pinyin,
                meaning=meaning,
                part_of_speech="",
                usage_notes="",
            )
        )

    return TableParseResult(cards=cards, unparsed_lines=unparsed)

