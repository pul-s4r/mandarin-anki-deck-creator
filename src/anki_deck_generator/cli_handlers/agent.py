"""CLI commands for the desktop AnkiWeb pull agent."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from anki_deck_generator.agent.config import AgentConfig, default_config_path, write_agent_config
from anki_deck_generator.agent.loop import discover_inflight, load_cursor
from anki_deck_generator.agent.paths import (
    agent_cache_dir,
    agent_data_dir,
    agent_state_dir,
    agent_venv_python,
    default_config_path,
    platform_label,
)


def _repo_agent_script() -> Path:
    return Path(__file__).resolve().parents[3] / "scripts" / "ankiweb-pull-agent" / "agent.py"


def _render_template(template_path: Path, mapping: dict[str, str]) -> str:
    text = template_path.read_text(encoding="utf-8")
    for key, value in mapping.items():
        text = text.replace(key, value)
    return text


def _register_agent(*, server_url: str, agent_id: str, register_secret: str) -> str:
    import httpx

    resp = httpx.post(
        f"{server_url.rstrip('/')}/api/ankiweb/agent/register",
        json={"agent_id": agent_id, "register_secret": register_secret},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return str(data["token"])


def _revoke_agent(*, server_url: str, agent_id: str, register_secret: str) -> None:
    import httpx

    resp = httpx.post(
        f"{server_url.rstrip('/')}/api/ankiweb/agent/revoke",
        params={"register_secret": register_secret},
        json={"agent_id": agent_id},
        timeout=30.0,
    )
    resp.raise_for_status()


def _create_venv(data_dir: Path) -> Path:
    venv_dir = data_dir / "venv"
    if not venv_dir.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    py = agent_venv_python()
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    req = _repo_agent_script().parent / "requirements.txt"
    subprocess.run([str(py), "-m", "pip", "install", "-r", str(req)], check=True)
    return py


def _install_init_unit(*, python_path: Path, agent_script: Path, config_path: Path) -> None:
    init_dir = _repo_agent_script().parent / "init"
    mapping = {
        "__VENV_PYTHON__": str(python_path),
        "__AGENT_SCRIPT__": str(agent_script),
        "__AGENT_CONFIG__": str(config_path),
        "__STDOUT_LOG__": str(agent_state_dir() / "agent.stdout"),
        "__STDERR_LOG__": str(agent_state_dir() / "agent.stderr"),
    }
    label = platform_label()
    if label == "macos":
        plist_src = init_dir / "com.anki-notes-pipeline.agent.plist"
        target = Path.home() / "Library" / "LaunchAgents" / "com.anki-notes-pipeline.agent.plist"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_template(plist_src, mapping), encoding="utf-8")
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(target)], check=False)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(target)], check=True)
        return
    if label == "linux":
        unit_src = init_dir / "anki-notes-pipeline-agent.service"
        target = Path.home() / ".config" / "systemd" / "user" / "anki-notes-pipeline-agent.service"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_template(unit_src, mapping), encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "anki-notes-pipeline-agent.service"], check=True)
        return
    xml_src = init_dir / "AnkiNotesPipelineAgent.xml"
    xml_out = agent_data_dir() / "AnkiNotesPipelineAgent.xml"
    xml_out.write_text(_render_template(xml_src, mapping), encoding="utf-16")
    subprocess.run(
        ["schtasks", "/Create", "/F", "/TN", "AnkiNotesPipelineAgent", "/XML", str(xml_out)],
        check=True,
    )


def _uninstall_init_unit() -> None:
    label = platform_label()
    if label == "macos":
        target = Path.home() / "Library" / "LaunchAgents" / "com.anki-notes-pipeline.agent.plist"
        if target.is_file():
            uid = os.getuid()
            subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(target)], check=False)
            target.unlink()
        return
    if label == "linux":
        subprocess.run(["systemctl", "--user", "disable", "--now", "anki-notes-pipeline-agent.service"], check=False)
        target = Path.home() / ".config" / "systemd" / "user" / "anki-notes-pipeline-agent.service"
        if target.is_file():
            target.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        return
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", "AnkiNotesPipelineAgent"], check=False)


def run_agent_setup(args: argparse.Namespace) -> int:
    server_url = args.server_url or input("Server URL: ").strip()
    agent_id = args.agent_id or input("Agent id (e.g. desktop-laptop): ").strip()
    register_secret = args.register_secret or input("Register secret: ").strip()
    token = _register_agent(server_url=server_url, agent_id=agent_id, register_secret=register_secret)
    config = AgentConfig(
        server_url=server_url,
        agent_id=agent_id,
        agent_token=token,
        anki_connect_url=args.anki_connect_url,
        deck_name=args.deck_name,
        model_name=args.model_name,
    )
    cfg_path = default_config_path()
    write_agent_config(config, cfg_path)
    data_dir = agent_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    agent_script = data_dir / "agent.py"
    shutil.copy2(_repo_agent_script(), agent_script)
    py = _create_venv(data_dir)
    _install_init_unit(python_path=py, agent_script=agent_script, config_path=cfg_path)
    print(f"agent configured at {cfg_path}")
    print(f"venv python: {py}")
    return 0


def run_agent_status(_args: argparse.Namespace) -> int:
    cfg_path = default_config_path()
    if not cfg_path.is_file():
        print("agent config not found; run `anki-notes-pipeline agent setup` first", file=sys.stderr)
        return 1
    cache = agent_cache_dir()
    inflight = discover_inflight(cache)
    print(f"config: {cfg_path}")
    print(f"cursor: {load_cursor(cache)!r}")
    print(f"inflight_batch: {inflight}")
    if inflight is not None:
        payload = json.loads(inflight.read_text(encoding="utf-8"))
        print(f"inflight_ack_items: {len(payload.get('ack_body', {}).get('results', []))}")
    return 0


def run_agent_uninstall(args: argparse.Namespace) -> int:
    _uninstall_init_unit()
    data_dir = agent_data_dir()
    if data_dir.is_dir():
        shutil.rmtree(data_dir)
    cache = agent_cache_dir()
    if cache.is_dir():
        shutil.rmtree(cache)
    cfg_path = default_config_path()
    if args.purge and cfg_path.is_file():
        cfg_path.unlink()
    if not args.purge:
        print(f"preserved config at {cfg_path}")
    return 0


def run_agent_rebuild_venv(_args: argparse.Namespace) -> int:
    data_dir = agent_data_dir()
    venv_dir = data_dir / "venv"
    if venv_dir.is_dir():
        shutil.rmtree(venv_dir)
    py = _create_venv(data_dir)
    print(f"rebuilt venv at {py}")
    return 0


def run_agent_revoke(args: argparse.Namespace) -> int:
    cfg_path = default_config_path()
    if not cfg_path.is_file():
        print("agent config not found", file=sys.stderr)
        return 1
    from anki_deck_generator.agent.config import load_agent_config

    config = load_agent_config(cfg_path)
    register_secret = args.register_secret or input("Register secret: ").strip()
    _revoke_agent(
        server_url=config.server_url,
        agent_id=config.agent_id,
        register_secret=register_secret,
    )
    print(f"revoked agent {config.agent_id}")
    return 0


def run_agent_command(args: argparse.Namespace) -> int:
    if args.agent_command == "setup":
        return run_agent_setup(args)
    if args.agent_command == "status":
        return run_agent_status(args)
    if args.agent_command == "uninstall":
        return run_agent_uninstall(args)
    if args.agent_command == "rebuild-venv":
        return run_agent_rebuild_venv(args)
    if args.agent_command == "revoke":
        return run_agent_revoke(args)
    return 1
