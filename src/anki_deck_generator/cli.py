from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.pipeline import run_pipeline


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="anki-notes-pipeline", description="Chinese notes → Anki vocabulary CSV")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run extraction pipeline")
    run.add_argument("input", type=Path, help="Input PDF, Markdown, or DOCX file")
    run.add_argument("--output", "-o", type=Path, required=True, help="Output CSV path")
    run.add_argument("--cedict-path", type=Path, default=None, help="Path to cedict_ts.u8")
    run.add_argument("--chunk-size", type=int, default=None)
    run.add_argument("--chunk-overlap", type=int, default=None)
    run.add_argument("--csv-bom", action="store_true", help="Write UTF-8 BOM for Excel")
    run.add_argument("--no-skip-lines-filter", action="store_true", help="Disable date-only line dropping")
    run.add_argument("--cedict-force-overwrite", action="store_true", help="Overwrite LLM meaning/pinyin from CEDICT")
    run.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "run":
        settings = Settings()
        if args.chunk_size is not None:
            settings.chunk_size = args.chunk_size
        if args.chunk_overlap is not None:
            settings.chunk_overlap = args.chunk_overlap
        if args.csv_bom:
            settings.csv_bom = True
        if args.no_skip_lines_filter:
            settings.skip_lines_filter = False
        if args.cedict_path is not None:
            settings.cedict_path = args.cedict_path
        if args.cedict_force_overwrite:
            settings.cedict_force_overwrite = True
        run_pipeline(args.input.resolve(), args.output.resolve(), settings)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
