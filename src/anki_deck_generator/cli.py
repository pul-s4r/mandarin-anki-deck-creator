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
    run.add_argument("--prior-csv", type=Path, default=None, help="Optional prior exported CSV for term index")
    run.add_argument("--sentence-links-csv", type=Path, default=None, help="Write sentence_links.csv to this path")
    run.add_argument(
        "--enable-sentences",
        dest="enable_sentences",
        action="store_true",
        help="Enable dialogue sentence parsing + linking (default)",
    )
    run.add_argument(
        "--disable-sentences",
        dest="enable_sentences",
        action="store_false",
        help="Disable dialogue sentence parsing + linking",
    )
    run.add_argument(
        "--sentence-assignment-strategy",
        choices=["importance", "random"],
        default=None,
        help="When multiple terms match a sentence, pick winner by 'importance' (default) or 'random'",
    )
    run.add_argument("--sentence-random-seed", type=int, default=None, help="Seed for random sentence assignment")
    run.add_argument(
        "--sentences-per-term",
        type=int,
        default=None,
        help="Max number of sentences to store per term in the main CSV (default 1)",
    )
    run.add_argument(
        "--sentences-delimiter",
        type=str,
        default=None,
        help="Delimiter when storing multiple sentences per term (default: ' | ')",
    )
    run.add_argument("--chunk-size", type=int, default=None)
    run.add_argument("--chunk-overlap", type=int, default=None)
    run.add_argument("--csv-bom", action="store_true", help="Write UTF-8 BOM for Excel")
    run.add_argument("--no-skip-lines-filter", action="store_true", help="Disable date-only line dropping")
    run.add_argument("--cedict-force-overwrite", action="store_true", help="Overwrite LLM meaning/pinyin from CEDICT")
    run.add_argument(
        "--no-decomposition-fallback",
        dest="enable_decomposition_fallback",
        action="store_false",
        help="Disable greedy CEDICT decomposition when exact headword is missing",
    )
    run.add_argument(
        "--no-llm-translation-fallback",
        dest="enable_llm_translation_fallback",
        action="store_false",
        help="Disable Bedrock batch translation for rows still missing English after enrichment",
    )
    run.set_defaults(
        enable_sentences=True,
        enable_decomposition_fallback=True,
        enable_llm_translation_fallback=True,
    )
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
        settings.enable_sentences = bool(args.enable_sentences)
        settings.enable_decomposition_fallback = bool(args.enable_decomposition_fallback)
        settings.enable_llm_translation_fallback = bool(args.enable_llm_translation_fallback)
        if args.prior_csv is not None:
            settings.prior_csv = args.prior_csv
        if args.sentence_links_csv is not None:
            settings.sentence_links_csv = args.sentence_links_csv
        if args.sentence_assignment_strategy is not None:
            settings.sentence_assignment_strategy = args.sentence_assignment_strategy
        if args.sentence_random_seed is not None:
            settings.sentence_random_seed = args.sentence_random_seed
        if args.sentences_per_term is not None:
            settings.sentences_per_term = args.sentences_per_term
        if args.sentences_delimiter is not None:
            settings.sentences_delimiter = args.sentences_delimiter
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
