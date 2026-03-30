from __future__ import annotations

from anki_deck_generator.preprocess.chunk import chunk_text
from anki_deck_generator.preprocess.normalize import normalize_unicode, optional_drop_metadata_lines


def test_nfkC_compatibility_variant() -> None:
    # compatibility ideograph vs regular (when present in string)
    s = normalize_unicode("⼦")
    assert s


def test_chunk_overlap() -> None:
    text = "a" * 100
    chunks = chunk_text(text, chunk_size=40, overlap=10)
    assert len(chunks) >= 3
    assert all(len(c) <= 40 for c in chunks)


def test_optional_drop_skips_date_only_line() -> None:
    raw = "20/03\n1. 动脑 dong3 nao3 - think\n"
    out = optional_drop_metadata_lines(raw, enabled=True)
    assert "动脑" in out
