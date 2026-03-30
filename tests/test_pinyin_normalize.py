from __future__ import annotations

from anki_deck_generator.dictionary.pinyin_normalize import cedict_pinyin_to_tone_marks


def test_numeric_tones_to_marks() -> None:
    assert cedict_pinyin_to_tone_marks("xian2 rou4") == "xián ròu"


def test_neutral_tone() -> None:
    assert cedict_pinyin_to_tone_marks("de5") == "de"
