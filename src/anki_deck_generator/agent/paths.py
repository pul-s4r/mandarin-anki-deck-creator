"""Platform-specific install paths for the desktop pull agent."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def agent_data_dir() -> Path:
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(local) / "AnkiNotesPipeline" / "agent"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "anki-notes-pipeline" / "agent"


def agent_state_dir() -> Path:
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(local) / "AnkiNotesPipeline" / "state"
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / "anki-notes-pipeline"


def agent_cache_dir() -> Path:
    return agent_data_dir() / "cache"


def agent_venv_python() -> Path:
    if sys.platform == "win32":
        return agent_data_dir() / "venv" / "Scripts" / "python.exe"
    return agent_data_dir() / "venv" / "bin" / "python"


def default_config_path() -> Path:
    from anki_deck_generator.agent.config import default_config_path as _cfg

    return _cfg()


def platform_label() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"
