"""
Regenerate baseline artifacts (run from repo root with dev dependencies).

  python tests/baselines/record.py
  # CI baselines: tests/baselines/inputs/sample.{md,docx,pdf}

  # Personal E2E fixture (stub vocabulary per chunk; no Bedrock):
  python tests/baselines/record.py \\
    --add ~/Documents/mandarin-notes-sample-1.pdf \\
    --add ~/Documents/mandarin-notes-sample-2.pdf \\
    -o ~/Documents/llm_mock_e2e.json

Writes:
  - tests/baselines/inputs/sample.docx / sample.pdf (if missing)
  - tests/baselines/llm_mock.json (chunk hashes → vocabulary cards)
  - tests/baselines/outputs/*.csv (expected script-mode outputs)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
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


def _record_paths(
    paths: list[Path],
    *,
    settings: Settings,
) -> dict[str, str]:
    """Run pipeline with a stub LLM; return chunk_hash → chunk_text."""
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
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            for inp in paths:
                if not inp.is_file():
                    raise FileNotFoundError(f"Input not found: {inp}")
                out = tmp_dir / f"{inp.name}.csv"
                run_pipeline(inp.resolve(), out, settings)
    finally:
        pipeline_mod.extract_vocabulary_from_chunk = prev_extract  # type: ignore[method-assign]
        pipeline_mod.build_bedrock_model = prev_build  # type: ignore[method-assign]

    return recorded_hashes


def _chunks_payload(recorded_hashes: dict[str, str]) -> dict[str, list[dict]]:
    return {h: [_CARD.model_dump()] for h in sorted(recorded_hashes)}


def _load_fixture_chunks(path: Path) -> dict[str, list[dict]]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): list(v) for k, v in (raw.get("chunks") or {}).items()}


def _write_fixture(path: Path, chunks: dict[str, list[dict]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"chunks": chunks, "translations": {}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _e2e_settings() -> Settings:
    """Match manual E2E: skip_lines_filter on, sentences off."""
    return Settings(
        cedict_path=CEDICT,
        skip_lines_filter=True,
        enable_sentences=False,
    )


def record_personal_fixture(add_paths: list[Path], output: Path, *, merge: Path | None) -> None:
    settings = _e2e_settings()
    recorded = _record_paths(add_paths, settings=settings)
    chunks = _load_fixture_chunks(merge) if merge else {}
    chunks.update(_chunks_payload(recorded))
    _write_fixture(output, chunks)
    print(f"Wrote {len(recorded)} chunk(s) from {len(add_paths)} file(s) → {output}")


def record_ci_baselines() -> None:
    _ensure_docx()
    _ensure_pdf()

    settings = Settings(
        cedict_path=CEDICT,
        skip_lines_filter=False,
        enable_sentences=False,
    )
    ci_inputs = [INPUTS / name for name in ("sample.md", "sample.docx", "sample.pdf")]
    recorded_hashes = _record_paths(ci_inputs, settings=settings)
    chunks_payload = _chunks_payload(recorded_hashes)

    LLM_MOCK.write_text(
        json.dumps({"chunks": chunks_payload, "translations": {}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    settings2 = Settings(
        cedict_path=CEDICT,
        skip_lines_filter=False,
        enable_sentences=False,
        llm_fixture_path=LLM_MOCK,
    )
    for inp in ci_inputs:
        out = OUTPUTS / f"{inp.name}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        run_pipeline(inp, out, settings2)

    print("Wrote", LLM_MOCK, "and outputs under", OUTPUTS)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Record LLM fixture JSON for deterministic pipeline runs.")
    p.add_argument(
        "--add",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Input file to record (repeatable). With -o, writes a personal E2E fixture.",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Fixture output path (required when using --add).",
    )
    p.add_argument(
        "--merge",
        type=Path,
        default=None,
        help="Existing fixture JSON to merge new chunks into (optional).",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if args.add:
        if args.output is None:
            print("error: --output is required when using --add", file=sys.stderr)
            sys.exit(1)
        record_personal_fixture(args.add, args.output.resolve(), merge=args.merge)
        return
    if args.output or args.merge:
        print("error: --output/--merge require at least one --add", file=sys.stderr)
        sys.exit(1)
    record_ci_baselines()


if __name__ == "__main__":
    main()
