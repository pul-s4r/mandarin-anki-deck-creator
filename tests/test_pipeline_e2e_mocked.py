from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.llm.schemas import LlmVocabularyItem
from anki_deck_generator.pipeline import run_pipeline


def test_run_pipeline_csv_with_cedict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    md = tmp_path / "notes.md"
    md.write_text("1. 的 de - possessive\n", encoding="utf-8")
    cedict = tmp_path / "cedict.u8"
    cedict.write_text("的 的 [de5] /possessive particle/\n", encoding="utf-8")
    out = tmp_path / "out.csv"

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.build_bedrock_model",
        lambda _settings: MagicMock(),
    )

    def fake_extract(_model, chunk: str) -> list[LlmVocabularyItem]:
        assert "的" in chunk
        return [
            LlmVocabularyItem(
                simplified="的",
                traditional="",
                pinyin="",
                meaning="",
                part_of_speech="particle",
                usage_notes="",
            )
        ]

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.extract_vocabulary_from_chunk",
        fake_extract,
    )
    settings = Settings(cedict_path=cedict, skip_lines_filter=False, enable_sentences=False)
    run_pipeline(md, out, settings)
    data = out.read_text(encoding="utf-8")
    assert "的" in data
    assert "possessive" in data
