from __future__ import annotations

import csv

from anki_deck_generator.dictionary.enrich import VocabularyRow
from anki_deck_generator.export.csv_writer import (
    vocabulary_csv_bytes,
    write_vocabulary_csv,
)


def test_write_csv_headers(tmp_path) -> None:
    path = tmp_path / "out.csv"
    rows = [
        VocabularyRow(
            key=1,
            simplified="的",
            traditional="的",
            pinyin="de",
            meaning="particle",
            part_of_speech="particle",
            usage_notes="",
        )
    ]
    write_vocabulary_csv(path, rows, bom=False)
    text = path.read_text(encoding="utf-8")
    assert "SentenceSimplified" in text
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        assert r.fieldnames == [
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
        ]


def test_vocabulary_csv_bytes_matches_write_and_bom_prefix(tmp_path) -> None:
    rows = [
        VocabularyRow(
            key=1,
            simplified="的",
            traditional="的",
            pinyin="de",
            meaning="particle",
            part_of_speech="particle",
            usage_notes="",
        )
    ]
    path = tmp_path / "out.csv"
    write_vocabulary_csv(path, rows, bom=False)
    assert path.read_bytes() == vocabulary_csv_bytes(rows, bom=False)

    path_bom = tmp_path / "out_bom.csv"
    write_vocabulary_csv(path_bom, rows, bom=True)
    raw = vocabulary_csv_bytes(rows, bom=True)
    assert raw.startswith(b"\xef\xbb\xbf")
    assert path_bom.read_bytes() == raw
