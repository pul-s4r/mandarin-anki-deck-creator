from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.llm.schemas import LlmVocabularyItem
from anki_deck_generator.pipeline import run_pipeline, run_pipeline_from_text


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

    def fake_extract(_model, chunk: str) -> tuple[list[LlmVocabularyItem], bool]:
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
        ], True

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.extract_vocabulary_from_chunk",
        fake_extract,
    )
    settings = Settings(cedict_path=cedict, skip_lines_filter=False, enable_sentences=False)
    run_pipeline(md, out, settings)
    data = out.read_text(encoding="utf-8")
    assert "的" in data
    assert "possessive" in data


def test_run_pipeline_llm_translation_fallback_without_cedict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md = tmp_path / "notes.md"
    md.write_text("lesson\n", encoding="utf-8")
    out = tmp_path / "out.csv"

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.build_bedrock_model",
        lambda _settings: MagicMock(),
    )

    def fake_extract(_model, chunk: str) -> tuple[list[LlmVocabularyItem], bool]:
        assert "lesson" in chunk
        return [
            LlmVocabularyItem(
                simplified="生词",
                traditional="",
                pinyin="",
                meaning="",
                part_of_speech="",
                usage_notes="",
            )
        ], True

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.extract_vocabulary_from_chunk",
        fake_extract,
    )

    def fake_translate(_model, terms: list[str]) -> tuple[dict[str, str], bool]:
        assert terms == ["生词"]
        return {"生词": "new word"}, True

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.translate_simplified_terms",
        fake_translate,
    )

    settings = Settings(skip_lines_filter=False, enable_sentences=False, enable_llm_translation_fallback=True)
    run_pipeline(md, out, settings)
    data = out.read_text(encoding="utf-8")
    assert "生词" in data
    assert "new word" in data
    assert "LLM translation fallback" in data


def test_run_pipeline_chunks_failed_on_llm_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md = tmp_path / "notes.md"
    md.write_text("lesson\n", encoding="utf-8")
    out = tmp_path / "out.csv"

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.build_bedrock_model",
        lambda _settings: MagicMock(),
    )

    def fake_extract(_model, chunk: str) -> tuple[list[LlmVocabularyItem], bool]:
        return [], False

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.extract_vocabulary_from_chunk",
        fake_extract,
    )

    settings = Settings(skip_lines_filter=False, enable_sentences=False)
    run_pipeline(md, out, settings)
    data = out.read_text(encoding="utf-8")
    # CSV should be header-only (no cards extracted due to LLM failure)
    assert data.strip().startswith("Key,")
    assert "生词" not in data


def test_run_pipeline_cedict_decomposition_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md = tmp_path / "notes.md"
    md.write_text("lesson\n", encoding="utf-8")
    cedict = tmp_path / "cedict.u8"
    cedict.write_text(
        "团圆 团圆 [tuan2 yuan2] /reunion/\n饭 饭 [fan4] /meal/\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.csv"

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.build_bedrock_model",
        lambda _settings: MagicMock(),
    )

    def fake_extract(_model, chunk: str) -> tuple[list[LlmVocabularyItem], bool]:
        return [
            LlmVocabularyItem(
                simplified="团圆饭",
                traditional="",
                pinyin="",
                meaning="",
                part_of_speech="",
                usage_notes="",
            )
        ], True

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.extract_vocabulary_from_chunk",
        fake_extract,
    )

    called: dict[str, bool] = {"translate": False}

    def fake_translate(_model, terms: list[str]) -> tuple[dict[str, str], bool]:
        called["translate"] = True
        return {}, True

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.translate_simplified_terms",
        fake_translate,
    )

    settings = Settings(
        cedict_path=cedict,
        skip_lines_filter=False,
        enable_sentences=False,
        enable_llm_translation_fallback=True,
    )
    run_pipeline(md, out, settings)
    assert called["translate"] is True

    data = out.read_text(encoding="utf-8")
    assert "团圆饭" in data
    assert "reunion" in data.lower()
    assert "meal" in data.lower()
    assert "CEDICT decomposition" in data


def test_run_pipeline_unreadable_cedict_skips_enrichment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md = tmp_path / "notes.md"
    md.write_text("lesson\n", encoding="utf-8")
    cedict = tmp_path / "cedict.u8"
    cedict.write_text("garbage binary\xff\xfe", encoding="utf-8")
    out = tmp_path / "out.csv"

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.build_bedrock_model",
        lambda _settings: MagicMock(),
    )

    def fake_extract(_model, chunk: str) -> tuple[list[LlmVocabularyItem], bool]:
        return [
            LlmVocabularyItem(
                simplified="生词",
                traditional="",
                pinyin="",
                meaning="",
                part_of_speech="",
                usage_notes="",
            )
        ], True

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.extract_vocabulary_from_chunk",
        fake_extract,
    )

    settings = Settings(cedict_path=cedict, skip_lines_filter=False, enable_sentences=False)
    run_pipeline(md, out, settings)
    data = out.read_text(encoding="utf-8")
    assert "生词" in data
    assert "enriched_count" not in data  # enrichment skipped, meaning stays empty


def test_finish_pipeline_unreadable_cedict_no_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anki_deck_generator.dictionary.source import FileLineDictionarySource

    md = tmp_path / "notes.md"
    md.write_text("lesson\n", encoding="utf-8")
    cedict = tmp_path / "cedict.u8"
    cedict.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.build_bedrock_model",
        lambda _settings: MagicMock(),
    )

    def fake_extract(_model, chunk: str) -> tuple[list[LlmVocabularyItem], bool]:
        return [
            LlmVocabularyItem(
                simplified="生词",
                traditional="",
                pinyin="",
                meaning="",
                part_of_speech="",
                usage_notes="",
            )
        ], True

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.extract_vocabulary_from_chunk",
        fake_extract,
    )

    orig_init = FileLineDictionarySource.__init__

    def broken_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        raise OSError("permission denied")

    monkeypatch.setattr(FileLineDictionarySource, "__init__", broken_init)

    settings = Settings(cedict_path=cedict, skip_lines_filter=False, enable_sentences=False)
    result = run_pipeline_from_text("lesson\n", settings)
    assert result.stats.enriched_count == 0
