"""Shared CLI helpers used by multiple subcommands."""

from __future__ import annotations

import argparse

from anki_deck_generator.config.settings import Settings


def apply_run_like_settings(settings: Settings, args: argparse.Namespace) -> None:
    if getattr(args, "cedict_path", None) is not None:
        settings.cedict_path = args.cedict_path
    if getattr(args, "chunk_size", None) is not None:
        settings.chunk_size = args.chunk_size
    if getattr(args, "chunk_overlap", None) is not None:
        settings.chunk_overlap = args.chunk_overlap
    if getattr(args, "csv_bom", False):
        settings.csv_bom = True
    if getattr(args, "no_skip_lines_filter", False):
        settings.skip_lines_filter = False
    if getattr(args, "llm_fixture_path", None) is not None:
        settings.llm_fixture_path = args.llm_fixture_path
    if hasattr(args, "enable_sentences"):
        settings.enable_sentences = bool(args.enable_sentences)
