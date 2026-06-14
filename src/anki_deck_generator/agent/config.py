"""Pull-agent configuration (agent.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentConfig:
    server_url: str
    agent_id: str
    agent_token: str
    anki_connect_url: str = "http://127.0.0.1:8765"
    anki_connect_api_key: str | None = None
    deck_name: str = "Chinese vocabulary"
    model_name: str = "Chinese vocabulary"
    conflict_policy: str = "prefer-remote"
    idle_interval_s: float = 60.0
    active_interval_s: float = 5.0
    active_window_s: float = 120.0
    max_backoff_s: float = 300.0
    startup_probe_max_s: float = 300.0


def default_config_path() -> Path:
    env = os.environ.get("ANKI_AGENT_CONFIG")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "anki-notes-pipeline" / "agent.toml"


def load_agent_config(path: Path | None = None) -> AgentConfig:
    cfg_path = path or default_config_path()
    data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    return AgentConfig(
        server_url=str(data["server_url"]).rstrip("/"),
        agent_id=str(data["agent_id"]),
        agent_token=str(data["agent_token"]),
        anki_connect_url=str(data.get("anki_connect_url", "http://127.0.0.1:8765")),
        anki_connect_api_key=data.get("anki_connect_api_key"),
        deck_name=str(data.get("deck_name", "Chinese vocabulary")),
        model_name=str(data.get("model_name", "Chinese vocabulary")),
        conflict_policy=str(data.get("conflict_policy", "prefer-remote")),
        idle_interval_s=float(data.get("idle_interval_s", 60)),
        active_interval_s=float(data.get("active_interval_s", 5)),
        active_window_s=float(data.get("active_window_s", 120)),
        max_backoff_s=float(data.get("max_backoff_s", 300)),
        startup_probe_max_s=float(data.get("startup_probe_max_s", 300)),
    )


def write_agent_config(config: AgentConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'server_url = "{config.server_url}"',
        f'agent_id = "{config.agent_id}"',
        f'agent_token = "{config.agent_token}"',
        f'anki_connect_url = "{config.anki_connect_url}"',
        f'deck_name = "{config.deck_name}"',
        f'model_name = "{config.model_name}"',
        f'conflict_policy = "{config.conflict_policy}"',
        f"idle_interval_s = {config.idle_interval_s}",
        f"active_interval_s = {config.active_interval_s}",
        f"active_window_s = {config.active_window_s}",
        f"max_backoff_s = {config.max_backoff_s}",
        f"startup_probe_max_s = {config.startup_probe_max_s}",
    ]
    if config.anki_connect_api_key:
        lines.append(f'anki_connect_api_key = "{config.anki_connect_api_key}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
