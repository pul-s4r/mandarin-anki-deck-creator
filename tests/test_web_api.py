from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from anki_deck_generator.config.settings import ServerSettings, Settings
from anki_deck_generator.llm.schemas import LlmVocabularyItem
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.web.app import create_app
from anki_deck_generator.web.dependencies import get_server_settings, get_settings, get_state_store

REPO_ROOT = Path(__file__).resolve().parents[1]
CEDICT = REPO_ROOT / "tests" / "baselines" / "cedict_sample.u8"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()

    settings = Settings(
        state_backend="sqlite",
        state_db_path=db,
        cedict_path=CEDICT,
        skip_lines_filter=False,
        enable_sentences=False,
    )

    server_settings = ServerSettings(max_upload_size_mb=1)
    app = create_app(server_settings=server_settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_state_store] = lambda: store
    app.dependency_overrides[get_server_settings] = lambda: server_settings

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.build_bedrock_model",
        lambda _settings: MagicMock(),
    )

    def fake_extract(_model, chunk: str) -> tuple[list[LlmVocabularyItem], bool]:
        if "的" in chunk:
            return [
                LlmVocabularyItem(
                    simplified="的",
                    traditional="",
                    pinyin="",
                    meaning="",
                    part_of_speech="particle",
                    usage_notes="",
                )
            ], True
        return [], True

    monkeypatch.setattr(
        "anki_deck_generator.pipeline.extract_vocabulary_from_chunk",
        fake_extract,
    )

    with TestClient(app) as test_client:
        yield test_client

    store.close()


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sync_run_processes_upload(client: TestClient) -> None:
    content = "1. 的 de - possessive\n".encode()
    response = client.post(
        "/api/sync/run",
        files={"file": ("sample.md", content, "text/markdown")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["stats"]["deduped_card_count"] >= 1
    assert any(row["simplified"] == "的" for row in body["rows"])
    persistence = body["persistence"]
    assert persistence is not None
    assert persistence["run_id"]
    assert persistence["cards_created"] >= 1

    run_resp = client.get(f"/api/sync/runs/{persistence['run_id']}")
    assert run_resp.status_code == 200
    assert run_resp.json()["trigger"] == "api-upload"


def test_sync_run_rejects_oversized_upload(client: TestClient) -> None:
    huge = b"x" * (2 * 1024 * 1024)
    response = client.post(
        "/api/sync/run",
        files={"file": ("sample.md", huge, "text/markdown")},
    )
    assert response.status_code == 413


def test_sync_run_populates_agent_pending_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()
    settings = Settings(state_backend="sqlite", state_db_path=db, skip_lines_filter=False, enable_sentences=False)
    server_settings = ServerSettings(
        agent_register_secret="register-secret",
        ankiweb_deck_name="D",
        ankiweb_model_name="Chinese vocabulary",
    )
    app = create_app(server_settings=server_settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_state_store] = lambda: store
    app.dependency_overrides[get_server_settings] = lambda: server_settings

    monkeypatch.setattr("anki_deck_generator.pipeline.build_bedrock_model", lambda _settings: MagicMock())

    def fake_extract(_model, chunk: str) -> tuple[list[LlmVocabularyItem], bool]:
        if "词" in chunk:
            return [LlmVocabularyItem(simplified="词", meaning="word")], True
        return [], True

    monkeypatch.setattr("anki_deck_generator.pipeline.extract_vocabulary_from_chunk", fake_extract)
    with TestClient(app) as api_client:
        reg = api_client.post(
            "/api/ankiweb/agent/register",
            json={"agent_id": "desktop", "register_secret": "register-secret"},
        )
        token = reg.json()["token"]
        upload = api_client.post(
            "/api/sync/run",
            files={"file": ("notes.md", "1. 词 ci - word\n".encode(), "text/markdown")},
        )
        assert upload.status_code == 200
        assert upload.json()["persistence"]["cards_created"] == 1

        pending = api_client.get(
            "/api/ankiweb/pending",
            params={"agent_id": "desktop"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert pending.status_code == 200
        items = pending.json()["items"]
        assert len(items) == 1
        assert items[0]["anki"]["fields"]["Simplified"] == "词"
    store.close()


def test_sync_run_requires_state_backend(tmp_path: Path) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(state_backend="none")
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/sync/run",
            files={"file": ("sample.md", b"1. test\n", "text/markdown")},
        )
    assert response.status_code == 503
