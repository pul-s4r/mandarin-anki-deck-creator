"""Desktop AnkiWeb pull-agent polling loop."""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from anki_deck_generator.agent.config import AgentConfig
from anki_deck_generator.export.ankiweb.anki_connect import AnkiConnectClient
from anki_deck_generator.export.ankiweb.exporter import ANKI_MODEL_FIELDS
from anki_deck_generator.export.ankiweb.merge import three_way_merge

logger = logging.getLogger(__name__)


class AgentState(StrEnum):
    WAITING_FOR_ANKI = "waiting_for_anki"
    IDLE = "idle"
    APPLYING = "applying"


class CloudClient(Protocol):
    def fetch_pending(self, *, cursor: str) -> dict[str, Any]: ...

    def post_ack(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class HttpxCloudClient:
    def __init__(self, config: AgentConfig) -> None:
        import httpx

        self._config = config
        self._http = httpx.Client(timeout=60.0)

    def close(self) -> None:
        self._http.close()

    def fetch_pending(self, *, cursor: str) -> dict[str, Any]:
        params = {"agent_id": self._config.agent_id, "cursor": cursor}
        headers = {"Authorization": f"Bearer {self._config.agent_token}"}
        resp = self._http.get(f"{self._config.server_url}/api/ankiweb/pending", params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def post_ack(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._config.agent_token}"}
        resp = self._http.post(f"{self._config.server_url}/api/ankiweb/ack", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


@dataclass
class AgentRuntime:
    config: AgentConfig
    cloud: CloudClient
    anki: AnkiConnectClient
    cache_dir: Path
    state: AgentState = AgentState.WAITING_FOR_ANKI
    cursor: str = ""
    active_until: float = 0.0
    backoff_s: float = 1.0
    inflight_path: Path | None = None
    last_pending_count: int = 0
    last_error: str = ""
    applied_log: Path = field(default_factory=Path)


def _inflight_dir(cache_dir: Path) -> Path:
    d = cache_dir / "inflight_batches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cursor_path(cache_dir: Path) -> Path:
    return cache_dir / "last_cursor"


def load_cursor(cache_dir: Path) -> str:
    path = _cursor_path(cache_dir)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def save_cursor(cache_dir: Path, cursor: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    _cursor_path(cache_dir).write_text(cursor, encoding="utf-8")


def discover_inflight(cache_dir: Path) -> Path | None:
    inflight = _inflight_dir(cache_dir)
    files = sorted(inflight.glob("*.json"))
    return files[0] if files else None


def stage_inflight(cache_dir: Path, batch_id: str, payload: dict[str, Any]) -> Path:
    path = _inflight_dir(cache_dir) / f"{batch_id}.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def clear_inflight(path: Path) -> None:
    if path.is_file():
        path.unlink()


def anki_is_ready(client: AnkiConnectClient) -> bool:
    try:
        if client.version() < 6:
            return False
        perm = client.request_permission()
        return perm.get("permission") == "granted"
    except Exception:
        return False


def _flatten_remote_fields(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in raw.items():
        if isinstance(val, dict) and "value" in val:
            out[str(key)] = str(val.get("value") or "")
        else:
            out[str(key)] = str(val or "")
    return out


def apply_batch_item(
    client: AnkiConnectClient,
    item: dict[str, Any],
    *,
    conflict_policy: str,
) -> dict[str, Any]:
    card_id = str(item["card_id"])
    op = str(item.get("op", "create"))
    anki = dict(item.get("anki", {}))
    base_fields = item.get("base_fields")
    local_fields = {k: str(v) for k, v in dict(anki.get("fields", {})).items()}
    tag_query = f'tag:"ext_id:{card_id}"'
    note_id: int | None = None
    try:
        if op == "create":
            can_add = client.can_add_notes_with_error_detail([anki])
            detail = can_add[0] if can_add else {"canAdd": False, "error": "empty response"}
            if detail.get("canAdd"):
                created = client.add_notes([anki])
                raw_id = created[0] if created else None
                if raw_id is None:
                    return {"card_id": card_id, "op": op, "status": "error", "error": "addNotes returned nil"}
                note_id = int(raw_id)
                return {
                    "card_id": card_id,
                    "op": op,
                    "status": "applied",
                    "anki_note_id": note_id,
                    "applied_fields": local_fields,
                }
            found = client.find_notes(tag_query)
            if not found:
                return {
                    "card_id": card_id,
                    "op": op,
                    "status": "error",
                    "error": str(detail.get("error", "cannot add note")),
                }
            note_id = int(found[0])
            op = "update"
        if note_id is None:
            found = client.find_notes(tag_query)
            if not found:
                can_add = client.can_add_notes_with_error_detail([anki])
                if can_add and can_add[0].get("canAdd"):
                    created = client.add_notes([anki])
                    raw_id = created[0] if created else None
                    if raw_id is None:
                        return {"card_id": card_id, "op": op, "status": "error", "error": "addNotes returned nil"}
                    return {
                        "card_id": card_id,
                        "op": "create",
                        "status": "applied",
                        "anki_note_id": int(raw_id),
                        "applied_fields": local_fields,
                    }
                return {"card_id": card_id, "op": op, "status": "error", "error": "note not found"}
            note_id = int(found[0])
        infos = client.notes_info([note_id])
        if not infos:
            return {"card_id": card_id, "op": op, "status": "error", "error": "notesInfo empty"}
        remote = _flatten_remote_fields(infos[0].get("fields"))
        merge = three_way_merge(
            base_fields=base_fields,
            remote_fields=remote,
            local_fields=local_fields,
            conflict_policy=conflict_policy,
            field_names=ANKI_MODEL_FIELDS,
        )
        if merge.has_conflict and conflict_policy == "tag-and-skip":
            return {
                "card_id": card_id,
                "op": op,
                "status": "conflict",
                "anki_note_id": note_id,
                "conflict": {"fields": merge.conflicted_field_names, "chosen": "remote"},
            }
        if not merge.has_update:
            return {"card_id": card_id, "op": op, "status": "unchanged", "anki_note_id": note_id}
        client.update_note_fields(note_id=note_id, fields=merge.merged_fields)
        return {
            "card_id": card_id,
            "op": op,
            "status": "applied",
            "anki_note_id": note_id,
            "applied_fields": merge.merged_fields,
        }
    except Exception as exc:
        return {"card_id": card_id, "op": op, "status": "error", "error": str(exc)}


def apply_pending_batch(runtime: AgentRuntime, batch: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    results = [
        apply_batch_item(runtime.anki, item, conflict_policy=runtime.config.conflict_policy)
        for item in batch.get("items", [])
    ]
    sync_status = ""
    if results:
        try:
            runtime.anki.sync()
            sync_status = "ok"
        except Exception as exc:
            sync_status = f"failed: {exc}"
    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "batch_id": batch.get("batch_id", ""),
        "agent_id": runtime.config.agent_id,
        "results": results,
        "sync_requested": bool(results),
        "sync_status": sync_status,
        "duration_ms": duration_ms,
    }


def replay_inflight(runtime: AgentRuntime, inflight_path: Path) -> bool:
    payload = json.loads(inflight_path.read_text(encoding="utf-8"))
    ack_body = payload.get("ack_body")
    if not isinstance(ack_body, dict):
        return False
    try:
        runtime.cloud.post_ack(ack_body)
        clear_inflight(inflight_path)
        cursor = payload.get("cursor", "")
        if cursor:
            save_cursor(runtime.cache_dir, cursor)
            runtime.cursor = cursor
        return True
    except Exception as exc:
        runtime.last_error = str(exc)
        logger.warning("inflight ack replay failed: %s", exc)
        return False


def run_once(runtime: AgentRuntime) -> AgentState:
    inflight = discover_inflight(runtime.cache_dir)
    if inflight is not None:
        runtime.state = AgentState.APPLYING
        replay_inflight(runtime, inflight)
        return runtime.state

    if not anki_is_ready(runtime.anki):
        runtime.state = AgentState.WAITING_FOR_ANKI
        return runtime.state

    runtime.state = AgentState.IDLE
    pending = runtime.cloud.fetch_pending(cursor=runtime.cursor)
    runtime.last_pending_count = len(pending.get("items", []))
    if not pending.get("items"):
        return runtime.state

    runtime.state = AgentState.APPLYING
    ack_body = apply_pending_batch(runtime, pending)
    cursor = str(pending.get("cursor", ""))
    inflight_path = stage_inflight(
        runtime.cache_dir,
        str(pending.get("batch_id", "unknown")),
        {"ack_body": ack_body, "cursor": cursor},
    )
    runtime.inflight_path = inflight_path
    runtime.cloud.post_ack(ack_body)
    clear_inflight(inflight_path)
    if cursor:
        save_cursor(runtime.cache_dir, cursor)
        runtime.cursor = cursor
    runtime.active_until = time.monotonic() + runtime.config.active_window_s
    log_path = runtime.cache_dir / "applied_log.ndjson"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now(UTC).isoformat(), **ack_body}, sort_keys=True) + "\n")
    return runtime.state


def sleep_interval(runtime: AgentRuntime) -> float:
    if runtime.state == AgentState.WAITING_FOR_ANKI:
        return min(runtime.backoff_s, runtime.config.max_backoff_s)
    if time.monotonic() < runtime.active_until:
        return runtime.config.active_interval_s
    return runtime.config.idle_interval_s


def run_loop(runtime: AgentRuntime, *, max_iterations: int | None = None) -> None:
    runtime.cursor = load_cursor(runtime.cache_dir)
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        try:
            prev = runtime.state
            state = run_once(runtime)
            if state == AgentState.WAITING_FOR_ANKI:
                runtime.backoff_s = min(runtime.backoff_s * 2, runtime.config.max_backoff_s)
            elif prev == AgentState.WAITING_FOR_ANKI and state != AgentState.WAITING_FOR_ANKI:
                runtime.backoff_s = 1.0
            runtime.last_error = ""
        except Exception as exc:
            runtime.last_error = str(exc)
            logger.exception("agent loop error")
            runtime.backoff_s = min(max(runtime.backoff_s * 2, 1.0), runtime.config.max_backoff_s)
        delay = sleep_interval(runtime)
        delay += random.uniform(0, min(delay * 0.1, 1.0))
        time.sleep(delay)
        iterations += 1
