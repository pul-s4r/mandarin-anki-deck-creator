from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.llm.schemas import LlmVocabularyItem
from anki_deck_generator.pipeline import run_pipeline


def test_sentences_disabled_does_not_write_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    md = tmp_path / "notes.md"
    md.write_text("Dialogues:\nA: 房东说下个月房租要涨5%。\n", encoding="utf-8")
    out = tmp_path / "out.csv"

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.build_bedrock_model",
        lambda _settings: MagicMock(),
    )
    monkeypatch.setattr(
        "anki_deck_generator.pipeline.extract_vocabulary_from_chunk",
        lambda _model, _chunk: [
            LlmVocabularyItem(
                simplified="房东",
                traditional="",
                pinyin="",
                meaning="",
                part_of_speech="",
                usage_notes="",
            ),
            LlmVocabularyItem(
                simplified="房租",
                traditional="",
                pinyin="",
                meaning="",
                part_of_speech="",
                usage_notes="",
            ),
        ],
    )

    settings = Settings(skip_lines_filter=False, enable_sentences=False)
    run_pipeline(md, out, settings)

    assert out.exists()
    assert not (tmp_path / "sentence_links.csv").exists()


def test_sentence_single_assignment_importance_prefers_longest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    md = tmp_path / "notes.md"
    md.write_text(
        "1. 房\n2. 房租\nDialogues:\nA: 下个月房租要涨5%。\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.csv"

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.build_bedrock_model",
        lambda _settings: MagicMock(),
    )

    def fake_extract(_model, chunk: str) -> list[LlmVocabularyItem]:
        # ensure vocab extraction still sees terms
        assert "房租" in chunk or "房\n" in chunk
        return [
            LlmVocabularyItem(simplified="房", traditional="", pinyin="", meaning="", part_of_speech="", usage_notes=""),
            LlmVocabularyItem(
                simplified="房租",
                traditional="",
                pinyin="",
                meaning="",
                part_of_speech="",
                usage_notes="",
            ),
        ]

    monkeypatch.setattr("anki_deck_generator.pipeline.extract_vocabulary_from_chunk", fake_extract)

    settings = Settings(skip_lines_filter=False, enable_sentences=True, sentences_per_term=1)
    run_pipeline(md, out, settings)

    sidecar = tmp_path / "sentence_links.csv"
    assert sidecar.exists()
    text = sidecar.read_text(encoding="utf-8")
    # Should assign to 房租 (longer term) not 房
    assert "房租要涨5%" in text

