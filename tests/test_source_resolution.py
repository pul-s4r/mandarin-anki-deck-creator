from __future__ import annotations

from pathlib import Path

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.config.source_sets import LocalFileSource
from anki_deck_generator.preprocess.fingerprints import sha256_bytes
from anki_deck_generator.state.records import SourceRecord
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.sync.source_resolution import resolve_local_file_source


def test_resolve_skips_unchanged_raw_bytes(tmp_path: Path) -> None:
    md = tmp_path / "n.md"
    md.write_text("hello", encoding="utf-8")
    raw = md.read_bytes()
    h = sha256_bytes(raw)

    db = tmp_path / "s.db"
    store = SqliteStateStore(db)
    store.init_schema()
    from anki_deck_generator.sync.source_ids import make_source_id

    ext = str(md.resolve())
    sid = make_source_id(user_id="default", provider="local-filesystem", external_id=ext)
    store.upsert_source_record(
        SourceRecord(
            source_id=sid,
            provider="local-filesystem",
            external_id=ext,
            content_sha256=h,
            user_id="default",
        )
    )

    src = LocalFileSource(provider="local-filesystem", path=md, external_id=ext)
    settings = Settings(skip_lines_filter=False)
    r = resolve_local_file_source(src, settings=settings, state_store=store, user_id="default")
    assert r.skipped_document is True
    assert r.normalized_text == ""
    store.close()


def test_resolve_ingests_when_new(tmp_path: Path) -> None:
    md = tmp_path / "n.md"
    md.write_text("# Title\n\nbody", encoding="utf-8")
    db = tmp_path / "s.db"
    store = SqliteStateStore(db)
    store.init_schema()
    ext = str(md.resolve())
    src = LocalFileSource(provider="local-filesystem", path=md, external_id=ext)
    settings = Settings(skip_lines_filter=False)
    r = resolve_local_file_source(src, settings=settings, state_store=store, user_id="default")
    assert r.skipped_document is False
    assert "body" in r.normalized_text
    assert r.ingest_format == "markdown"
    store.close()
