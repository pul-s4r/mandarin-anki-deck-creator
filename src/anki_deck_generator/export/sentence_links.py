from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SentenceLinkRow:
    sentence_id: str
    sentence_simplified: str
    sentence_traditional: str
    sentence_pinyin: str
    sentence_meaning: str
    linked_key: int
    source: str
    match_debug: str = ""


FIELDNAMES = (
    "SentenceId",
    "SentenceSimplified",
    "SentenceTraditional",
    "SentencePinyin",
    "SentenceMeaning",
    "LinkedKey",
    "Source",
    "MatchDebug",
)


def sentence_links_csv_bytes(rows: list[SentenceLinkRow]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDNAMES)
    w.writeheader()
    for r in rows:
        w.writerow(
            {
                "SentenceId": r.sentence_id,
                "SentenceSimplified": r.sentence_simplified,
                "SentenceTraditional": r.sentence_traditional,
                "SentencePinyin": r.sentence_pinyin,
                "SentenceMeaning": r.sentence_meaning,
                "LinkedKey": r.linked_key,
                "Source": r.source,
                "MatchDebug": r.match_debug,
            }
        )
    return buf.getvalue().encode("utf-8")


def write_sentence_links_csv(path: Path, rows: list[SentenceLinkRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(sentence_links_csv_bytes(rows))

