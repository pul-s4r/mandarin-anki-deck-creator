"""Persistence-aware incremental sync orchestrator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from anki_deck_generator.config.source_sets import LocalFileSource, SourceSet
from anki_deck_generator.export.base import Exporter
from anki_deck_generator.ingest.router import extract_text_from_bytes
from anki_deck_generator.llm.bedrock_chain import build_bedrock_model
from anki_deck_generator.pipeline import (
    PipelineResult,
    PipelineStats,
    dedupe_llm_items,
    extract_llm_vocabulary_items,
    finish_pipeline_after_llm,
)
from anki_deck_generator.preprocess.blocks import segment_table_blocks
from anki_deck_generator.preprocess.chunk import chunk_text
from anki_deck_generator.preprocess.fingerprints import sha256_bytes, sha256_utf8
from anki_deck_generator.preprocess.normalize import normalize_unicode, optional_drop_metadata_lines
from anki_deck_generator.preprocess.tables import parse_table_block
from anki_deck_generator.state.records import CardUpsertResult, ChunkRecord, RunReportRecord, SourceRecord
from anki_deck_generator.state.store import StateStore
from anki_deck_generator.sync.cards_bridge import card_record_to_llm_item, card_records_to_pipeline_rows, vocabulary_row_to_card_record
from anki_deck_generator.sync.report import SyncReport, SyncReportStats, SyncRunOutcome
from anki_deck_generator.sync.source_ids import make_source_id

if TYPE_CHECKING:
    from anki_deck_generator.config.settings import Settings


def _suffix_to_format(suffix: str) -> str | None:
    s = suffix.lower()
    if s == ".pdf":
        return "pdf"
    if s in {".md", ".markdown"}:
        return "markdown"
    if s == ".docx":
        return "docx"
    return None


def _persist_chunk_records(
    *,
    sid: str,
    text: str,
    settings: Settings,
    state_store: StateStore,
    user_id: str,
    per_source: dict[int, list],
) -> None:
    """Write ChunkRecord rows with SHA-256 matching extract_llm_vocabulary_items."""
    now = datetime.now(UTC)
    blocks = segment_table_blocks(text)
    text_chunk_lists: list[list[str]] = []
    for block in blocks:
        if block.kind == "table":
            continue
        text_chunk_lists.append(
            chunk_text(block.text, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
        )

    seq = 0
    t_idx = 0
    for block in blocks:
        if block.kind == "table":
            parsed = parse_table_block(block.text)
            needs_fallback = len(parsed.cards) < 2 or len(parsed.unparsed_lines) >= max(3, len(parsed.cards))
            if needs_fallback:
                sha = sha256_utf8(block.text)
                ids: list[str] = []
                for it in per_source.get(seq, []):
                    cr = state_store.get_card_by_key(it.simplified.strip(), user_id=user_id)
                    if cr:
                        ids.append(cr.card_id)
                state_store.upsert_processed_chunk(
                    ChunkRecord(
                        source_id=sid,
                        chunk_index=seq,
                        chunk_sha256=sha,
                        processed_at=now,
                        model_id=settings.bedrock_model_id,
                        llm_output_card_ids=ids,
                        user_id=user_id,
                    )
                )
                seq += 1
            continue
        chunks = text_chunk_lists[t_idx]
        t_idx += 1
        for chunk in chunks:
            sha = sha256_utf8(chunk)
            ids = []
            for it in per_source.get(seq, []):
                cr = state_store.get_card_by_key(it.simplified.strip(), user_id=user_id)
                if cr:
                    ids.append(cr.card_id)
            state_store.upsert_processed_chunk(
                ChunkRecord(
                    source_id=sid,
                    chunk_index=seq,
                    chunk_sha256=sha,
                    processed_at=now,
                    model_id=settings.bedrock_model_id,
                    llm_output_card_ids=ids,
                    user_id=user_id,
                )
            )
            seq += 1


def run_incremental_sync(
    source_set: SourceSet,
    *,
    settings: Settings,
    state_store: StateStore,
    exporters: list[Exporter],
    only_file_ids: list[str] | None = None,
    user_id: str = "default",
) -> SyncReport:
    """
    Process each source in the set, persist cards, run exporters.

    Document-level skip: unchanged raw file bytes (SHA-256) short-circuit before ingest.
    Chunk-level skip: unchanged chunk text SHA-256 reuses cached per-chunk card IDs.
    """
    run_id = str(uuid.uuid4())
    started = datetime.now(UTC)
    report = SyncReport(
        run_id=run_id,
        run_started_at=started,
        run_finished_at=None,
        outcomes=[],
        stats=SyncReportStats(),
    )

    model = build_bedrock_model(settings)
    chunk_cards: dict[str, dict[int, list]] = {}

    for src in source_set.sources:
        if not isinstance(src, LocalFileSource):
            continue
        if only_file_ids is not None and src.external_id not in only_file_ids:
            continue

        sid = make_source_id(user_id=user_id, provider=src.provider, external_id=src.external_id)
        raw = src.path.read_bytes()
        file_hash = sha256_bytes(raw)
        prev = state_store.get_source_record(src.provider, src.external_id)
        if prev is not None and prev.content_sha256 == file_hash:
            report.outcomes.append(
                SyncRunOutcome(
                    source_id=sid,
                    external_id=src.external_id,
                    skipped_document=True,
                )
            )
            report.stats.documents_skipped += 1
            continue

        fmt = _suffix_to_format(src.path.suffix)
        if fmt is None:
            raise ValueError(f"Unsupported file type for incremental sync: {src.path}")

        text = extract_text_from_bytes(raw, format=fmt)
        text = normalize_unicode(text)
        text = optional_drop_metadata_lines(text, enabled=settings.skip_lines_filter)

        chunk_cards[sid] = {}

        def _should_run_llm(seq: int, sha: str) -> bool:
            rec = state_store.get_processed_chunk(sid, seq)
            if rec is None:
                return True
            return rec.chunk_sha256 != sha

        def _load_cached(seq: int):
            rec = state_store.get_processed_chunk(sid, seq)
            if rec is None:
                return []
            out_items = []
            for cid in rec.llm_output_card_ids:
                cr = state_store.get_card_by_id(cid)
                if cr is not None:
                    out_items.append(card_record_to_llm_item(cr))
            return out_items

        def _on_chunk(seq: int, _sha: str, items: list) -> None:
            chunk_cards[sid][seq] = dedupe_llm_items(list(items))

        all_cards, total_llm, proc, skipped = extract_llm_vocabulary_items(
            text,
            settings,
            model=model,
            progress_callback=None,
            should_run_llm=_should_run_llm,
            load_cached_chunk_cards=_load_cached,
            on_chunk_processed=_on_chunk,
        )
        report.stats.chunks_processed += proc
        report.stats.chunks_skipped += skipped
        report.stats.sources_processed += 1

        result = finish_pipeline_after_llm(
            all_cards,
            text,
            settings,
            model=model,
            progress_callback=None,
            total_llm_chunks=total_llm,
        )

        outcome = SyncRunOutcome(source_id=sid, external_id=src.external_id, skipped_document=False)
        for row in result.rows:
            rec = vocabulary_row_to_card_record(row, source_id=sid, state_store=state_store, user_id=user_id)
            res = state_store.upsert_card(rec)
            if res is CardUpsertResult.CREATED:
                outcome.cards_created += 1
            elif res is CardUpsertResult.UPDATED:
                outcome.cards_updated += 1
            else:
                outcome.cards_unchanged += 1
        report.outcomes.append(outcome)

        now = datetime.now(UTC)
        mtime = str(src.path.stat().st_mtime_ns)
        state_store.upsert_source_record(
            SourceRecord(
                source_id=sid,
                provider=src.provider,
                external_id=src.external_id,
                revision_id=mtime,
                etag="",
                content_sha256=file_hash,
                last_ingested_at=now,
                user_id=user_id,
            )
        )

        _persist_chunk_records(
            sid=sid,
            text=text,
            settings=settings,
            state_store=state_store,
            user_id=user_id,
            per_source=chunk_cards[sid],
        )

    for exp in exporters:
        rows = list(state_store.iter_all_cards(user_id=user_id))
        vrows = card_records_to_pipeline_rows(rows)
        pr = PipelineResult(
            rows=vrows,
            sentence_links=[],
            stats=PipelineStats(
                block_count=0,
                chunk_count=0,
                raw_card_count=len(vrows),
                deduped_card_count=len(vrows),
                enriched_count=0,
                llm_translation_fallback_count=0,
                decomposition_fallback_count=0,
                sentence_link_count=0,
            ),
        )
        data = exp.export(pr)
        dest = getattr(exp, "output_path", None)
        if isinstance(dest, Path):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            report.export_paths.append(str(dest))

    finished = datetime.now(UTC)
    report.run_finished_at = finished
    state_store.record_run(
        RunReportRecord(
            run_id=run_id,
            trigger="schedule",
            started_at=started,
            finished_at=finished,
            sync_report_json=report.to_json(),
            user_id=user_id,
        )
    )
    return report
