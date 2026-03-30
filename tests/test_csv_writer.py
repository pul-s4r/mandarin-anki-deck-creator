from __future__ import annotations

import csv

from anki_deck_generator.dictionary.enrich import VocabularyRow
from anki_deck_generator.export.csv_writer import write_vocabulary_csv


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
    assert "Sentence" not in text
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
        ]
