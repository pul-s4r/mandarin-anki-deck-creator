from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_deck_generator.agent.loop import (
    AgentRuntime,
    apply_batch_item,
    discover_inflight,
    replay_inflight,
    run_once,
    stage_inflight,
)
from anki_deck_generator.agent.config import AgentConfig


@pytest.fixture
def runtime(tmp_path: Path) -> AgentRuntime:
    config = AgentConfig(
        server_url="http://testserver",
        agent_id="desktop",
        agent_token="tok",
    )
    cloud = MagicMock()
    anki = MagicMock()
    anki.version.return_value = 6
    anki.request_permission.return_value = {"permission": "granted"}
    return AgentRuntime(config=config, cloud=cloud, anki=anki, cache_dir=tmp_path)


def test_apply_batch_item_create(runtime: AgentRuntime) -> None:
    runtime.anki.can_add_notes_with_error_detail.return_value = [{"canAdd": True}]
    runtime.anki.add_notes.return_value = [999]
    item = {
        "card_id": "c1",
        "op": "create",
        "anki": {
            "deckName": "D",
            "modelName": "M",
            "fields": {"Simplified": "词", "Meaning": "m"},
            "tags": ["ext_id:c1"],
        },
        "base_fields": None,
    }
    result = apply_batch_item(runtime.anki, item, conflict_policy="prefer-remote")
    assert result["status"] == "applied"
    assert result["anki_note_id"] == 999
    assert result["applied_fields"]["Simplified"] == "词"


def test_run_once_idle_when_no_pending(runtime: AgentRuntime) -> None:
    runtime.cloud.fetch_pending.return_value = {"cursor": "", "batch_id": "", "items": []}
    state = run_once(runtime)
    assert state.value == "idle"


def test_replay_inflight_after_crash(runtime: AgentRuntime, tmp_path: Path) -> None:
    ack_body = {
        "batch_id": "b1",
        "agent_id": "desktop",
        "results": [{"card_id": "c1", "op": "create", "status": "applied", "anki_note_id": 1}],
        "sync_requested": True,
        "sync_status": "ok",
        "duration_ms": 1,
    }
    path = stage_inflight(tmp_path, "b1", {"ack_body": ack_body, "cursor": "2024-01-01T00:00:00+00:00|c1"})
    assert discover_inflight(tmp_path) == path
    runtime.cloud.post_ack.return_value = {"status": "ok"}
    assert replay_inflight(runtime, path) is True
    runtime.cloud.post_ack.assert_called_once_with(ack_body)
    assert discover_inflight(tmp_path) is None
