from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from anki_deck_generator.config.settings import ServerSettings, Settings
from anki_deck_generator.state.records import CardRecord, RunReportRecord
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.web.app import create_app
from anki_deck_generator.web.dependencies import get_server_settings, get_settings, get_state_store


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()

    settings = Settings(state_backend="sqlite", state_db_path=db)
    server_settings = ServerSettings(
        agent_register_secret="register-secret",
        ankiweb_deck_name="D",
        ankiweb_model_name="Chinese vocabulary",
        ankiweb_pending_batch_size=50,
    )
    app = create_app(server_settings=server_settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_state_store] = lambda: store
    app.dependency_overrides[get_server_settings] = lambda: server_settings

    with TestClient(app) as test_client:
        yield test_client

    store.close()


def test_register_and_pending_ack_flow(client: TestClient, tmp_path: Path) -> None:
    reg = client.post(
        "/api/ankiweb/agent/register",
        json={"agent_id": "desktop", "register_secret": "register-secret"},
    )
    assert reg.status_code == 200
    token = reg.json()["token"]

    now = datetime.now(UTC)
    store = SqliteStateStore(tmp_path / "state.db")
    store.upsert_card(
        CardRecord(
            card_id="c1",
            simplified="词",
            meaning="word",
            last_updated_at=now,
            first_seen_source_id="src",
        )
    )
    store.record_run(
        RunReportRecord(
            run_id="run-1",
            trigger="test",
            started_at=now,
            finished_at=now,
            sync_report_json='{"run_id":"run-1","exports":{}}',
        )
    )
    store.close()

    headers = {"Authorization": f"Bearer {token}"}
    pending = client.get("/api/ankiweb/pending", params={"agent_id": "desktop"}, headers=headers)
    assert pending.status_code == 200
    body = pending.json()
    assert body["batch_id"]
    assert len(body["items"]) == 1
    assert body["items"][0]["card_id"] == "c1"

    ack = client.post(
        "/api/ankiweb/ack",
        headers=headers,
        json={
            "batch_id": body["batch_id"],
            "agent_id": "desktop",
            "results": [
                {
                    "card_id": "c1",
                    "op": "create",
                    "status": "applied",
                    "anki_note_id": 42,
                    "applied_fields": {"Simplified": "词", "Meaning": "word"},
                }
            ],
            "sync_requested": True,
            "sync_status": "ok",
            "duration_ms": 5,
        },
    )
    assert ack.status_code == 200
    assert ack.json()["created"] == 1

    run_resp = client.get("/api/sync/runs/run-1")
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    assert len(run_body["exports_ankiweb"]) == 1
    assert run_body["exports_ankiweb"][0]["created"] == 1


def test_pending_requires_token(client: TestClient) -> None:
    resp = client.get("/api/ankiweb/pending", params={"agent_id": "desktop"})
    assert resp.status_code == 401


def test_ack_unknown_batch_is_409(client: TestClient) -> None:
    reg = client.post(
        "/api/ankiweb/agent/register",
        json={"agent_id": "desktop", "register_secret": "register-secret"},
    )
    token = reg.json()["token"]
    resp = client.post(
        "/api/ankiweb/ack",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "batch_id": "missing",
            "agent_id": "desktop",
            "results": [],
            "sync_requested": False,
            "sync_status": "",
        },
    )
    assert resp.status_code == 409
