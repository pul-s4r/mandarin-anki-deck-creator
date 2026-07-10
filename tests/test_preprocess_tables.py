from __future__ import annotations

from anki_deck_generator.llm.schemas import LlmVocabularyItem
from anki_deck_generator.preprocess.tables import parse_table_block


def test_parse_table_block_basic() -> None:
    text = "\u95ee\u9898\tproblem\tproblem meaning\n\u89e3\u51b3\u65b9\u6848\tsolution\tsolution meaning\n"
    result = parse_table_block(text)
    assert len(result.cards) == 2
    assert result.cards[0].simplified == "\u95ee\u9898"
    assert result.cards[0].pinyin == "problem"
    assert result.cards[0].meaning == "problem meaning"
    assert result.cards[1].simplified == "\u89e3\u51b3\u65b9\u6848"
    assert result.cards[1].meaning == "solution meaning"
    assert len(result.unparsed_lines) == 0


def test_parse_table_block_pinyin_continuation() -> None:
    text = "\u95ee\u9898\t\nwen ti\n\u5176\u4ed6\tstuff\tstuff meaning\n"
    result = parse_table_block(text)
    assert len(result.cards) == 2
    assert result.cards[0].pinyin == "wen ti"
    assert result.cards[0].meaning == ""


def test_parse_table_block_meaning_continuation() -> None:
    text = "\u95ee\u9898\tproblem\tproblem meaning\nadditional note\n\u5176\u4ed6\tstuff\tstuff meaning\n"
    result = parse_table_block(text)
    assert len(result.cards) == 2
    assert result.cards[0].meaning == "problem meaning additional note"


def test_parse_table_block_post_construction_mutation() -> None:
    item = LlmVocabularyItem(simplified="\u95ee\u9898", pinyin="", meaning="initial")
    item.pinyin = (item.pinyin + " " + "continuation").strip()
    item.meaning = (item.meaning + " " + "more").strip()
    assert item.pinyin == "continuation"
    assert item.meaning == "initial more"


def test_parse_table_block_needs_fallback() -> None:
    text = "line1\nline2\nline3\nline4\n"
    result = parse_table_block(text)
    assert len(result.cards) == 0
    assert len(result.unparsed_lines) == 4
