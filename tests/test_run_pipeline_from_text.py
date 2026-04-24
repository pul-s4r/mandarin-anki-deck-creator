from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.llm.schemas import LlmVocabularyItem
from anki_deck_generator.pipeline import run_pipeline_from_text


def test_run_pipeline_from_text_rows_and_progress_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    stages: list[tuple[str, int, int]] = []

    def cb(stage: str, cur: int, tot: int) -> None:
        stages.append((stage, cur, tot))

    cedict = tmp_path / "cedict.u8"
    cedict.write_text("的 的 [de5] /possessive particle/\n", encoding="utf-8")
    text = "1. 的 de - possessive\n"
    settings = Settings(
        cedict_path=cedict, skip_lines_filter=False, enable_sentences=False
    )
    result = run_pipeline_from_text(text, settings, progress_callback=cb)

    assert len(result.rows) == 1
    assert result.rows[0].simplified == "的"
    assert "possessive" in result.rows[0].meaning.lower()
    assert result.sentence_links == []
    assert result.stats.deduped_card_count == 1
    assert result.stats.raw_card_count >= 1

    assert stages[0][0] == "normalize"
    assert stages[-1][0] == "export"
    assert "enrich" in [s[0] for s in stages]
    chunk_stages = [s for s in stages if s[0] == "chunk"]
    llm_stages = [s for s in stages if s[0] == "llm"]
    assert [(s[1], s[2]) for s in chunk_stages] == [(s[1], s[2]) for s in llm_stages]
    assert len(chunk_stages) == len(llm_stages)
