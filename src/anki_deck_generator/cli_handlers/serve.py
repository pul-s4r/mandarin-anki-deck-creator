"""`serve` subcommand handler."""

from __future__ import annotations

import argparse
import sys


def run_serve_command(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "error: web server dependencies are not installed; use: pip install '.[server]'",
            file=sys.stderr,
        )
        return 1

    from anki_deck_generator.config.settings import ServerSettings
    from anki_deck_generator.web.app import create_app

    server_settings = ServerSettings()
    host = args.host if args.host is not None else server_settings.host
    port = args.port if args.port is not None else server_settings.port
    app = create_app(server_settings=server_settings)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0
