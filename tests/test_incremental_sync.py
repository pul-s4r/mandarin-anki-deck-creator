from __future__ import annotations

from pathlib import Path

import pytest

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.config.source_sets import SourceSet, LocalFileSource
from anki_deck_generator.export.exporters import VocabularyCsvFileExporter
from anki_deck_generator.ingest.router import extract_text_from_bytes
from anki_deck_generator.pipeline import run_pipeline_from_text
from anki_deck_generator.preprocess.llm_units import list_llm_text_units
from anki_deck_generator.preprocess.normalize import normalize_unicode, optional_drop_metadata_lines
from anki_deck_generator.state.records import compute_card_content_hash
from anki_deck_generator.state.sqlite_store import SqliteStateStore
import anki_deck_generator.pipeline as pipeline_module
from anki_deck_generator.sync.orchestrator import run_incremental_sync
from anki_deck_generator.sync.source_ids import make_source_id


def _card_semantic_sig_from_row(r: object) -> tuple[object, ...]:
    """Match CardRecord semantic fields + content_hash (sentence included)."""
    from anki_deck_generator.dictionary.enrich import VocabularyRow

    assert isinstance(r, VocabularyRow)
    h = compute_card_content_hash(
        simplified=r.simplified,
        traditional=r.traditional,
        pinyin=r.pinyin,
        meaning=r.meaning,
        part_of_speech=r.part_of_speech,
        usage_notes=r.usage_notes,
    )
    return (
        r.simplified.strip(),
        r.traditional.strip(),
        r.pinyin.strip(),
        r.meaning.strip(),
        r.part_of_speech.strip(),
        r.usage_notes.strip(),
        r.sentence_simplified.strip(),
        h,
    )


def _card_semantic_sig_from_record(c: object) -> tuple[object, ...]:
    from anki_deck_generator.state.records import CardRecord

    assert isinstance(c, CardRecord)
    return (
        c.simplified.strip(),
        c.traditional.strip(),
        c.pinyin.strip(),
        c.meaning.strip(),
        c.part_of_speech.strip(),
        c.usage_notes.strip(),
        c.sentence_simplified.strip(),
        c.content_hash,
    )


@pytest.fixture
def baselines() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1] / "tests" / "baselines"
    return root / "inputs" / "sample.md", root / "cedict_sample.u8"


def test_incremental_cold_matches_pipeline(tmp_path: Path, baselines: tuple[Path, Path]) -> None:
    md_path, cedict = baselines
    raw = md_path.read_bytes()
    text = extract_text_from_bytes(raw, format="markdown")
    text = normalize_unicode(text)
    text = optional_drop_metadata_lines(text, enabled=False)

    settings = Settings(
        llm_fixture_path=Path(__file__).resolve().parents[1] / "tests" / "baselines" / "llm_mock.json",
        cedict_path=cedict,
        enable_sentences=False,
        skip_lines_filter=False,
    )
    pr = run_pipeline_from_text(text, settings)

    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()

    ext_id = str(md_path.resolve())
    sset = SourceSet(
        name="t",
        sources=(LocalFileSource(provider="local-filesystem", path=md_path, external_id=ext_id),),
    )
    out_csv = tmp_path / "deck.csv"
    report = run_incremental_sync(
        sset,
        settings=settings,
        state_store=store,
        exporters=[VocabularyCsvFileExporter(output_path=out_csv, bom=False)],
    )
    assert report.stats.documents_skipped == 0
    assert report.stats.chunks_skipped == 0

    got = {_card_semantic_sig_from_record(c) for c in store.iter_all_cards()}
    exp = {_card_semantic_sig_from_row(r) for r in pr.rows}
    assert got == exp

    report2 = run_incremental_sync(
        sset,
        settings=settings,
        state_store=store,
        exporters=[VocabularyCsvFileExporter(output_path=out_csv, bom=False)],
    )
    assert report2.stats.documents_skipped == 1


def test_chunk_level_skips_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def spy(model: object, chunk: str) -> list:
        calls.append(chunk)
        from anki_deck_generator.llm.schemas import LlmVocabularyItem

        label = "A" if chunk.startswith("A") else "B"
        return [LlmVocabularyItem(simplified=label, meaning=label.lower(), traditional="", pinyin="", part_of_speech="", usage_notes="")]

    monkeypatch.setattr(pipeline_module, "extract_vocabulary_from_chunk", spy)

    md = tmp_path / "notes.md"
    # Two chunks with chunk_size=4 overlap=0 after normalize: "AAAA\n" is len 5 -> one chunk? 
    # Use 50-char runs so chunk_size=30 yields 2 chunks
    body_a = "A" * 50
    body_b = "B" * 50
    md.write_text(body_a + "\n" + body_b, encoding="utf-8")

    settings = Settings(
        chunk_size=30,
        chunk_overlap=0,
        enable_sentences=False,
        skip_lines_filter=False,
    )
    db = tmp_path / "state.db"
    store = SqliteStateStore(db)
    store.init_schema()
    ext_id = str(md.resolve())
    sset = SourceSet(
        name="t",
        sources=(LocalFileSource(provider="local-filesystem", path=md, external_id=ext_id),),
    )
    out = tmp_path / "o.csv"
    exp_path = VocabularyCsvFileExporter(output_path=out)

    run_incremental_sync(sset, settings=settings, state_store=store, exporters=[exp_path])
    first_calls = len(calls)
    assert first_calls >= 2

    raw = md.read_bytes()
    norm_text = normalize_unicode(extract_text_from_bytes(raw, format="markdown"))
    norm_text = optional_drop_metadata_lines(norm_text, enabled=settings.skip_lines_filter)
    sid = make_source_id(user_id="default", provider="local-filesystem", external_id=ext_id)
    expected_units = list_llm_text_units(norm_text, settings)
    for seq, u in enumerate(expected_units):
        ch = store.get_processed_chunk(sid, seq)
        assert ch is not None, seq
        assert ch.chunk_sha256 == u.chunk_sha256, seq

    calls.clear()
    rep_skip = run_incremental_sync(sset, settings=settings, state_store=store, exporters=[exp_path])
    assert len(calls) == 0
    assert rep_skip.stats.documents_skipped == 1

    md.write_text(body_a + "\n" + ("C" * 50), encoding="utf-8")
    calls.clear()
    rep = run_incremental_sync(sset, settings=settings, state_store=store, exporters=[exp_path])
    assert rep.stats.documents_skipped == 0
    assert rep.stats.chunks_skipped >= 1
    assert len(calls) >= 1
    assert len(calls) < first_calls
