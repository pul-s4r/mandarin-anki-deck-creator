from __future__ import annotations

import pytest

from anki_deck_generator.dictionary.parser import CedictParser


@pytest.fixture
def parser() -> CedictParser:
    return CedictParser()


def test_parse_basic(parser: CedictParser) -> None:
    line = "鹹肉 咸肉 [xian2 rou4] /bacon/salt-cured meat/"
    e = parser.parse_line(line)
    assert e is not None
    assert e.traditional == "鹹肉"
    assert e.simplified == "咸肉"
    assert e.pinyin_raw == "xian2 rou4"
    assert e.glosses == ("bacon", "salt-cured meat")


def test_parse_see_reference_brackets_in_gloss(parser: CedictParser) -> None:
    line = "鹹酥雞 咸酥鸡 [xian2 su1 ji1] /see 鹽酥雞|盐酥鸡[yan2 su1 ji1]/"
    e = parser.parse_line(line)
    assert e is not None
    assert e.simplified == "咸酥鸡"
    assert len(e.glosses) == 1
    assert "[yan2 su1 ji1]" in e.glosses[0]


def test_skip_comment(parser: CedictParser) -> None:
    assert parser.parse_line("# CC-CEDICT") is None


def test_skip_malformed_no_close_bracket(parser: CedictParser) -> None:
    assert parser.parse_line("的 的 [de /x/") is None


def test_skip_trailing_junk(parser: CedictParser) -> None:
    assert parser.parse_line("的 的 [de] /particle/ extra") is None


def test_empty_gloss_segments_dropped(parser: CedictParser) -> None:
    line = "的 的 [de5] /(possessive)/"
    e = parser.parse_line(line)
    assert e is not None
    assert e.glosses == ("(possessive)",)
