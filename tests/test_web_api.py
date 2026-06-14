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

    def fake_extract(_model, chunk: str) -> list[LlmVocabularyItem]:
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
            ]
        return []

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


def test_sync_run_rejects_oversized_upload(client: TestClient) -> None:
    huge = b"x" * (2 * 1024 * 1024)
    response = client.post(
        "/api/sync/run",
        files={"file": ("sample.md", huge, "text/markdown")},
    )
    assert response.status_code == 413


def test_sync_run_requires_state_backend(tmp_path: Path) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(state_backend="none")
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/sync/run",
            files={"file": ("sample.md", b"1. test\n", "text/markdown")},
        )
    assert response.status_code == 503
