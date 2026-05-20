"""`import` subcommand handler (external source providers)."""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from anki_deck_generator.errors import AnkiPipelineError, IntegrationError


def run_import_command(args: argparse.Namespace) -> int:
    from anki_deck_generator.integrations.registry import available_providers, get_provider

    importlib.import_module("anki_deck_generator.integrations.echo")
    importlib.import_module("anki_deck_generator.integrations.google_drive")
    if args.list_providers:
        for name in available_providers():
            print(name)
        return 0
    if not args.provider:
        print("error: pass a provider name or --list-providers", file=sys.stderr)
        return 1
    try:
        provider = get_provider(args.provider)
    except IntegrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.provider == "google-drive":
        if args.credentials_file is None:
            print("error: --credentials-file is required for google-drive", file=sys.stderr)
            return 1
        if args.output is None:
            print("error: --output is required for google-drive", file=sys.stderr)
            return 1
        if not args.folder_id and not args.file_ids:
            print("error: pass --folder-id and/or --file-id for google-drive", file=sys.stderr)
            return 1
        cred_path = Path(args.credentials_file).expanduser().resolve()
        out_dir = Path(args.output).expanduser().resolve()
        try:
            provider.authenticate({"credentials_file": str(cred_path)})
            result = provider.import_documents(folder_id=args.folder_id, file_ids=args.file_ids or None)
        except AnkiPipelineError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        out_dir.mkdir(parents=True, exist_ok=True)
        print(result.source_description)
        for doc in result.documents:
            safe_name = doc.filename.replace("/", "_").replace("\\", "_")
            dest = out_dir / safe_name
            dest.write_bytes(doc.data)
            print(f"  wrote {dest}\t{doc.format}\t{len(doc.data)} bytes\tid={doc.external_id}")
        return 0

    try:
        provider.authenticate({})
        result = provider.import_documents()
    except AnkiPipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.source_description)
    for doc in result.documents:
        print(f"  {doc.filename}\t{doc.format}\t{len(doc.data)} bytes\tid={doc.external_id}")
    return 0
