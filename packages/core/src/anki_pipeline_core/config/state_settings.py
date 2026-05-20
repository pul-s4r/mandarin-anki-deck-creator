from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


def default_state_db_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base).expanduser() / "anki-notes-pipeline" / "state.db"


class StateSettings(BaseSettings):
    """Persistence backend configuration shared across consumers."""

    model_config = SettingsConfigDict(
        env_prefix="ANKI_PIPELINE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    state_backend: Literal["none", "sqlite"] = "none"
    state_db_path: Optional[Path] = None
