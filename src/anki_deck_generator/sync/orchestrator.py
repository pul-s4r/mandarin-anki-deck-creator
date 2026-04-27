"""Persistence-aware incremental sync orchestrator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from anki_deck_generator.config.source_sets import LocalFileSource, SourceSet
from anki_deck_generator.export.base import Exporter
from anki_deck_generator.export.file_target import FileTargetExporter
from anki_deck_generator.llm.bedrock_chain import build_bedrock_model
from anki_deck_generator.pipeline import dedupe_llm_items, extract_llm_vocabulary_items, finish_pipeline_after_llm
from anki_deck_generator.preprocess.llm_units import list_llm_text_units
from anki_deck_generator.state.records import CardUpsertResult, ChunkRecord, RunReportRecord, SourceRecord
from anki_deck_generator.state.store import StateStore
from anki_deck_generator.sync.cards_bridge import card_record_to_llm_item, card_records_to_pipeline_rows, vocabulary_row_to_card_record
from anki_deck_generator.sync.change_detection import chunk_needs_llm
from anki_deck_generator.sync.report import SyncReport, SyncReportStats, SyncRunOutcome
from anki_deck_generator.sync.source_resolution import resolve_local_file_source

if TYPE_CHECKING:
    from anki_deck_generator.config.settings import Settings


def _persist_chunk_records(
    *,
    sid: str,
    text: str,
    settings: Settings,
    state_store: StateStore,
    user_id: str,
    per_source: dict[int, list],
) -> None:
    """Write ChunkRecord rows using the same LLM unit sequence as extract_llm_vocabulary_items."""
    now = datetime.now(UTC)
    for seq, unit in enumerate(list_llm_text_units(text, settings)):
        ids: list[str] = []
        for it in per_source.get(seq, []):
            cr = state_store.get_card_by_key(it.simplified.strip(), user_id=user_id)
            if cr:
                ids.append(cr.card_id)
        state_store.upsert_processed_chunk(
            ChunkRecord(
                source_id=sid,
                chunk_index=seq,
                chunk_sha256=unit.chunk_sha256,
                processed_at=now,
                model_id=settings.bedrock_model_id,
                llm_output_card_ids=ids,
                user_id=user_id,
            )
        )


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
    from anki_deck_generator.pipeline import PipelineResult, PipelineStats

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

        resolved = resolve_local_file_source(src, settings=settings, state_store=state_store, user_id=user_id)
        sid = resolved.source_id
        if resolved.skipped_document:
            report.outcomes.append(
                SyncRunOutcome(
                    source_id=sid,
                    external_id=src.external_id,
                    skipped_document=True,
                )
            )
            report.stats.documents_skipped += 1
            continue

        text = resolved.normalized_text

        chunk_cards[sid] = {}

        def _should_run_llm(seq: int, sha: str) -> bool:
            rec = state_store.get_processed_chunk(sid, seq)
            return chunk_needs_llm(rec, sha)

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
        state_store.upsert_source_record(
            SourceRecord(
                source_id=sid,
                provider=src.provider,
                external_id=src.external_id,
                revision_id=resolved.revision_id,
                etag="",
                content_sha256=resolved.raw_bytes_sha256,
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

    chunk_units_this_run = report.stats.chunks_processed + report.stats.chunks_skipped
    for exp in exporters:
        if not isinstance(exp, FileTargetExporter):
            raise TypeError(
                "run_incremental_sync requires FileTargetExporter (with output_path); "
                f"got {type(exp).__name__}"
            )
        rows = list(state_store.iter_all_cards(user_id=user_id))
        vrows = card_records_to_pipeline_rows(rows)
        # Store-derived export: stats reflect this sync run's LLM unit counts, not a full pipeline parse.
        pr = PipelineResult(
            rows=vrows,
            sentence_links=[],
            stats=PipelineStats(
                block_count=0,
                chunk_count=chunk_units_this_run,
                raw_card_count=len(vrows),
                deduped_card_count=len(vrows),
                enriched_count=0,
                llm_translation_fallback_count=0,
                decomposition_fallback_count=0,
                sentence_link_count=0,
            ),
        )
        data = exp.export(pr)
        dest = exp.output_path
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
