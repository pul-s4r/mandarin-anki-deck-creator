"""
Regenerate baseline artifacts (run from repo root with dev dependencies).

  uv run python tests/baselines/record.py
  # or: python tests/baselines/record.py

Writes:
  - tests/baselines/inputs/sample.docx / sample.pdf (if missing)
  - tests/baselines/llm_mock.json (chunk hashes → vocabulary cards)
  - tests/baselines/outputs/*.csv (expected script-mode outputs)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from unittest.mock import MagicMock  # noqa: E402

from anki_deck_generator.config.settings import Settings  # noqa: E402
from anki_deck_generator.llm.schemas import LlmVocabularyItem  # noqa: E402
from anki_deck_generator.pipeline import run_pipeline  # noqa: E402


INPUTS = ROOT / "tests/baselines/inputs"
OUTPUTS = ROOT / "tests/baselines/outputs"
CEDICT = ROOT / "tests/baselines/cedict_sample.u8"
LLM_MOCK = ROOT / "tests/baselines/llm_mock.json"

_CARD = LlmVocabularyItem(
    simplified="苹果",
    traditional="",
    pinyin="",
    meaning="apple",
    part_of_speech="noun",
    usage_notes="",
)


def _ensure_docx() -> None:
    path = INPUTS / "sample.docx"
    if path.is_file():
        return
    from docx import Document

    doc = Document()
    doc.add_paragraph("苹果")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


def _ensure_pdf() -> None:
    path = INPUTS / "sample.pdf"
    if path.is_file():
        return
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "苹果")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def main() -> None:
    _ensure_docx()
    _ensure_pdf()

    recorded_hashes: dict[str, str] = {}

    def fake_extract(model, chunk: str) -> list[LlmVocabularyItem]:
        h = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        recorded_hashes[h] = chunk
        return [_CARD]

    import anki_deck_generator.pipeline as pipeline_mod

    prev_extract = pipeline_mod.extract_vocabulary_from_chunk
    prev_build = pipeline_mod.build_bedrock_model
    pipeline_mod.extract_vocabulary_from_chunk = fake_extract  # type: ignore[method-assign]
    pipeline_mod.build_bedrock_model = lambda _settings: MagicMock()  # type: ignore[method-assign]

    try:
        settings = Settings(
            cedict_path=CEDICT,
            skip_lines_filter=False,
            enable_sentences=False,
        )
        for name in ("sample.md", "sample.docx", "sample.pdf"):
            inp = INPUTS / name
            out = OUTPUTS / f"{inp.name}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            run_pipeline(inp, out, settings)
    finally:
        pipeline_mod.extract_vocabulary_from_chunk = prev_extract  # type: ignore[method-assign]
        pipeline_mod.build_bedrock_model = prev_build  # type: ignore[method-assign]

    chunks_payload: dict[str, list[dict]] = {}
    for h, _chunk in sorted(recorded_hashes.items()):
        chunks_payload[h] = [_CARD.model_dump()]

    LLM_MOCK.write_text(
        json.dumps({"chunks": chunks_payload, "translations": {}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Re-run with fixture file to materialize committed CSVs (same bytes as recording path).
    settings2 = Settings(
        cedict_path=CEDICT,
        skip_lines_filter=False,
        enable_sentences=False,
        llm_fixture_path=LLM_MOCK,
    )
    for name in ("sample.md", "sample.docx", "sample.pdf"):
        inp = INPUTS / name
        out = OUTPUTS / f"{inp.name}.csv"
        run_pipeline(inp, out, settings2)

    print("Wrote", LLM_MOCK, "and outputs under", OUTPUTS)


if __name__ == "__main__":
    main()
