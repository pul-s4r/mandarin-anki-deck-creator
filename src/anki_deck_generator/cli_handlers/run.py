"""`run` subcommand handler."""

from __future__ import annotations

import argparse
import sys

from anki_deck_generator.cli_handlers.common import apply_run_like_settings
from anki_deck_generator.config.settings import Settings
from anki_deck_generator.errors import AnkiPipelineError
from anki_deck_generator.pipeline import run_pipeline


def run_run_command(args: argparse.Namespace) -> int:
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
    apply_run_like_settings(settings, args)
    if args.cedict_force_overwrite:
        settings.cedict_force_overwrite = True
    if not args.enable_decomposition_fallback:
        settings.enable_decomposition_fallback = False
    if not args.enable_llm_translation_fallback:
        settings.enable_llm_translation_fallback = False
    try:
        run_pipeline(args.input.resolve(), args.output.resolve(), settings)
    except AnkiPipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
