#!/usr/bin/env python3
"""Desktop AnkiWeb pull agent entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow running from a copied script path without package install.
_REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if _REPO_SRC.is_dir() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from anki_deck_generator.agent.config import default_config_path, load_agent_config
from anki_deck_generator.agent.loop import AgentRuntime, HttpxCloudClient, run_loop
from anki_deck_generator.agent.paths import agent_cache_dir
from anki_deck_generator.export.ankiweb.anki_connect import AnkiConnectClient


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_agent_config(default_config_path())
    cache_dir = agent_cache_dir()
    cloud = HttpxCloudClient(config)
    anki = AnkiConnectClient(base_url=config.anki_connect_url, api_key=config.anki_connect_api_key)
    runtime = AgentRuntime(config=config, cloud=cloud, anki=anki, cache_dir=cache_dir)
    try:
        run_loop(runtime)
    finally:
        cloud.close()
        anki.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
