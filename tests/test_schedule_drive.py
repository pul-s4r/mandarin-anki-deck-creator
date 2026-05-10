from __future__ import annotations

from pathlib import Path

import pytest

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.config.source_sets import GoogleDriveSource, SourceSet
from anki_deck_generator.export.exporters import VocabularyCsvFileExporter
from anki_deck_generator.integrations.base import ImportedDocument, ImportResult
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.sync import orchestrator as orch


class FakeDriveProvider:
    """Minimal stub; orchestrator calls authenticate, list_sources, fetch_file_metadata, import_documents."""

    name = "google-drive"

    def __init__(self) -> None:
        self.import_calls = 0

    def authenticate(self, credentials: dict) -> None:
        return None

    def list_sources(self, *, folder_id: str):
        return [
            {
                "id": "file-a",
                "name": "lesson.md",
                "mimeType": "text/markdown",
                "modifiedTime": "2024-01-01T00:00:00Z",
                "headRevisionId": "rev-a",
                "etag": "etag-a",
                "trashed": False,
            },
        ]

    def fetch_file_metadata(self, file_id: str):
        return {
            "id": file_id,
            "name": "orphan.md",
            "mimeType": "text/markdown",
            "modifiedTime": "2024-01-01T00:00:00Z",
            "headRevisionId": "rev-b",
            "etag": "etag-b",
            "trashed": False,
        }

    def import_documents(self, *, folder_id=None, file_ids=None):
        self.import_calls += 1
        assert file_ids is not None and len(file_ids) == 1
        fid = file_ids[0]
        base = Path(__file__).resolve().parents[1] / "tests" / "baselines" / "inputs"
        body = (base / "sample.md").read_bytes()
        return ImportResult(
            documents=[
                ImportedDocument(
                    filename="lesson.md",
                    format="markdown",
                    data=body,
                    external_id=fid,
                    revision_id="rev-a",
                    etag="etag-a",
                )
            ],
            source_description="fake",
        )


@pytest.fixture
def llm_settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[1] / "tests" / "baselines"
    return Settings(
        llm_fixture_path=root / "llm_mock.json",
        cedict_path=root / "cedict_sample.u8",
        enable_sentences=False,
        skip_lines_filter=False,
    )


def test_drive_incremental_skips_second_run_by_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, llm_settings: Settings
) -> None:
    fake = FakeDriveProvider()
    monkeypatch.setattr(orch, "drive_provider_factory", lambda: fake)

    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()
    cred = tmp_path / "cred.json"
    cred.write_text("{}", encoding="utf-8")

    sset = SourceSet(
        name="set",
        sources=(
            GoogleDriveSource(
                provider="google-drive",
                folder_ids=("fld",),
                file_ids=(),
                credentials_file=cred,
                external_id="ext",
            ),
        ),
    )
    out = tmp_path / "deck.csv"
    exp = VocabularyCsvFileExporter(output_path=out, bom=False)

    run = lambda: orch.run_incremental_sync(
        sset,
        settings=llm_settings,
        state_store=store,
        exporters=[exp],
    )

    r1 = run()
    assert fake.import_calls == 1
    assert r1.stats.documents_skipped == 0

    r2 = run()
    assert fake.import_calls == 1
    assert r2.stats.documents_skipped == 1


def test_drive_dry_run_cold_store_prints_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    llm_settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeDriveProvider()
    monkeypatch.setattr(orch, "drive_provider_factory", lambda: fake)

    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()
    cred = tmp_path / "cred.json"
    cred.write_text("{}", encoding="utf-8")

    sset = SourceSet(
        name="myset",
        sources=(
            GoogleDriveSource(
                provider="google-drive",
                folder_ids=("fld",),
                file_ids=(),
                credentials_file=cred,
                external_id="ext",
            ),
        ),
    )
    out = tmp_path / "deck.csv"

    report = orch.run_incremental_sync(
        sset,
        settings=llm_settings,
        state_store=store,
        exporters=[VocabularyCsvFileExporter(output_path=out, bom=False)],
        dry_run=True,
    )

    assert fake.import_calls == 0
    assert report.dry_run is True
    assert report.export_paths == []
    captured = capsys.readouterr().out
    assert "Dry run for source-set 'myset'" in captured
    assert "google-drive" in captured
    assert report.stats.sources_processed == 1
