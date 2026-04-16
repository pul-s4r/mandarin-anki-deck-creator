from __future__ import annotations

import csv
from pathlib import Path

from anki_deck_generator.dictionary.enrich import VocabularyRow

FIELDNAMES = (
    "Key",
    "Simplified",
    "Traditional",
    "Pinyin",
    "Meaning",
    "PartOfSpeech",
    "UsageNotes",
    "SentenceSimplified",
    "SentenceTraditional",
    "SentencePinyin",
    "SentenceMeaning",
)


def write_vocabulary_csv(path: Path, rows: list[VocabularyRow], *, bom: bool = False) -> None:
    encoding = "utf-8-sig" if bom else "utf-8"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding=encoding) as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "Key": r.key,
                    "Simplified": r.simplified,
                    "Traditional": r.traditional,
                    "Pinyin": r.pinyin,
                    "Meaning": r.meaning,
                    "PartOfSpeech": r.part_of_speech,
                    "UsageNotes": r.usage_notes,
                    "SentenceSimplified": r.sentence_simplified,
                    "SentenceTraditional": r.sentence_traditional,
                    "SentencePinyin": r.sentence_pinyin,
                    "SentenceMeaning": r.sentence_meaning,
                }
            )
