from __future__ import annotations

import csv
import io
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


def vocabulary_csv_bytes(rows: list[VocabularyRow], *, bom: bool = False) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES)
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
    body = buf.getvalue().encode("utf-8")
    if bom:
        return b"\xef\xbb\xbf" + body
    return body


def write_vocabulary_csv(
    path: Path, rows: list[VocabularyRow], *, bom: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(vocabulary_csv_bytes(rows, bom=bom))
