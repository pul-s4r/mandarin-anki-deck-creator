"""`auth` subcommand handler."""

from __future__ import annotations

import argparse
import sys

from anki_deck_generator.errors import AnkiPipelineError


def run_auth_command(args: argparse.Namespace) -> int:
    if args.auth_provider != "google-drive":
        print(f"error: unsupported auth provider {args.auth_provider!r}", file=sys.stderr)
        return 1
    from anki_deck_generator.integrations.google_drive import (
        default_google_drive_token_path,
        run_google_drive_oauth_and_save_token,
    )

    token_path = args.token_file.expanduser().resolve() if args.token_file else default_google_drive_token_path()
    try:
        run_google_drive_oauth_and_save_token(
            client_secrets=args.client_secrets.expanduser().resolve(),
            token_file=token_path,
        )
    except AnkiPipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote Google Drive credentials to {token_path}")
    return 0
