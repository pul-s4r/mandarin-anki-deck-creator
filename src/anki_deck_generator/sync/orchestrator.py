"""Persistence-aware incremental sync orchestrator."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from anki_deck_generator.config.source_sets import GoogleDriveSource, LocalFileSource, SourceSet
from anki_deck_generator.export.base import Exporter
from anki_deck_generator.export.file_target import FileTargetExporter
from anki_deck_generator.ingest.router import extract_text_from_bytes
from anki_deck_generator.llm.bedrock_chain import build_bedrock_model
from anki_deck_generator.pipeline import dedupe_llm_items, extract_llm_vocabulary_items, finish_pipeline_after_llm
from anki_deck_generator.pipeline_types import PipelineResult, PipelineStats
from anki_deck_generator.preprocess.fingerprints import sha256_bytes
from anki_deck_generator.preprocess.llm_units import list_llm_text_units
from anki_deck_generator.preprocess.normalize import normalize_unicode, optional_drop_metadata_lines
from anki_deck_generator.state.records import CardUpsertResult, ChunkRecord, RunReportRecord, SourceRecord
from anki_deck_generator.state.store import StateStore
from anki_deck_generator.sync.cards_bridge import (
    card_record_to_llm_item,
    card_records_to_pipeline_rows,
    vocabulary_row_to_card_record,
)
from anki_deck_generator.sync.change_detection import chunk_needs_llm, should_skip_document_by_stored_hash
from anki_deck_generator.sync.report import SyncReport, SyncReportStats, SyncRunOutcome
from anki_deck_generator.sync.source_ids import make_source_id
from anki_deck_generator.sync.source_resolution import resolve_local_file_source

if TYPE_CHECKING:
    from anki_deck_generator.config.settings import Settings

logger = logging.getLogger(__name__)


def drive_provider_factory() -> Any:
    """Lazy-load registry entry for ``google-drive`` (keeps CLI imports minimal)."""
    import importlib

    importlib.import_module("anki_deck_generator.integrations.google_drive")
    from anki_deck_generator.integrations.registry import get_provider

    return get_provider("google-drive")


def _drive_revision_unchanged(previous: SourceRecord | None, revision_id: str, etag: str) -> bool:
    if previous is None or not revision_id:
        return False
    if previous.revision_id != revision_id:
        return False
    if etag and previous.etag and previous.etag != etag:
        return False
    return True


def _collect_google_drive_metas(provider: object, src: GoogleDriveSource) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for folder_id in src.folder_ids:
        rows: list[dict[str, Any]] = provider.list_sources(folder_id=folder_id)  # type: ignore[union-attr]
        for row in rows:
            by_id[row["id"]] = row
    for fid in src.file_ids:
        if fid not in by_id:
            by_id[fid] = provider.fetch_file_metadata(fid)  # type: ignore[union-attr]
    return by_id


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


def _run_llm_pipeline_for_source(
    *,
    provider_key: str,
    external_id: str,
    revision_id: str,
    etag: str,
    content_sha256: str,
    normalized_text: str,
    settings: Settings,
    state_store: StateStore,
    model: object,
    user_id: str,
    chunk_cards: dict[str, dict[int, list]],
    report: SyncReport,
) -> None:
    sid = make_source_id(user_id=user_id, provider=provider_key, external_id=external_id)
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
        normalized_text,
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
        normalized_text,
        settings,
        model=model,
        progress_callback=None,
        total_llm_chunks=total_llm,
    )

    outcome = SyncRunOutcome(source_id=sid, external_id=external_id, skipped_document=False)
    for row in result.rows:
        existing = state_store.get_card_by_key(row.simplified.strip(), user_id=user_id)
        rec = vocabulary_row_to_card_record(row, source_id=sid, user_id=user_id, existing=existing)
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
            provider=provider_key,
            external_id=external_id,
            revision_id=revision_id,
            etag=etag,
            content_sha256=content_sha256,
            last_ingested_at=now,
            user_id=user_id,
        )
    )

    _persist_chunk_records(
        sid=sid,
        text=normalized_text,
        settings=settings,
        state_store=state_store,
        user_id=user_id,
        per_source=chunk_cards[sid],
    )


def run_incremental_sync(
    source_set: SourceSet,
    *,
    settings: Settings,
    state_store: StateStore,
    exporters: list[Exporter],
    only_file_ids: list[str] | None = None,
    user_id: str = "default",
    dry_run: bool = False,
) -> SyncReport:
    """
    Process each source in the set, persist cards, run exporters.

    Document-level skip: unchanged raw file bytes (SHA-256) short-circuit before ingest for locals;
    Google Drive prefers ``revision_id`` / ``etag`` metadata before downloading.

    Chunk-level skip: unchanged chunk text SHA-256 reuses cached per-chunk card IDs.

    ``dry_run``: print a short plan and skip LLM, persistence, exports, and run logging.
    """
    run_id = str(uuid.uuid4())
    started = datetime.now(UTC)
    report = SyncReport(
        run_id=run_id,
        run_started_at=started,
        run_finished_at=None,
        outcomes=[],
        stats=SyncReportStats(),
        dry_run=dry_run,
    )

    if dry_run:
        print(f"Dry run for source-set {source_set.name!r}:")

    model = None if dry_run else build_bedrock_model(settings)
    chunk_cards: dict[str, dict[int, list]] = {}

    for src in source_set.sources:
        if isinstance(src, LocalFileSource):
            if only_file_ids is not None and src.external_id not in only_file_ids:
                continue

            resolved = resolve_local_file_source(src, settings=settings, state_store=state_store, user_id=user_id)
            sid = resolved.source_id

            if dry_run:
                if resolved.skipped_document:
                    report.stats.documents_skipped += 1
                    report.outcomes.append(
                        SyncRunOutcome(source_id=sid, external_id=src.external_id, skipped_document=True)
                    )
                    print(f"  local unchanged: {src.path}")
                else:
                    report.stats.sources_processed += 1
                    report.outcomes.append(
                        SyncRunOutcome(source_id=sid, external_id=src.external_id, skipped_document=False)
                    )
                    print(f"  local would process: {src.path}")
                continue

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

            assert model is not None
            _run_llm_pipeline_for_source(
                provider_key=src.provider,
                external_id=src.external_id,
                revision_id=resolved.revision_id,
                etag="",
                content_sha256=resolved.raw_bytes_sha256,
                normalized_text=resolved.normalized_text,
                settings=settings,
                state_store=state_store,
                model=model,
                user_id=user_id,
                chunk_cards=chunk_cards,
                report=report,
            )
            continue

        if isinstance(src, GoogleDriveSource):
            provider = drive_provider_factory()
            provider.authenticate({"credentials_file": str(src.credentials_file)})
            metas = _collect_google_drive_metas(provider, src)

            would_change = 0
            would_skip = 0
            for fid, meta in metas.items():
                if meta.get("trashed"):
                    logger.warning("Skipping trashed Drive file %s (%r)", fid, meta.get("name"))
                    continue
                if only_file_ids is not None and fid not in only_file_ids:
                    continue
                prev = state_store.get_source_record("google-drive", fid, user_id=user_id)
                rev = str(meta.get("headRevisionId") or "")
                etag = str(meta.get("etag") or "")
                if _drive_revision_unchanged(prev, rev, etag):
                    would_skip += 1
                    if dry_run:
                        report.stats.documents_skipped += 1
                        sid = make_source_id(user_id=user_id, provider="google-drive", external_id=fid)
                        report.outcomes.append(
                            SyncRunOutcome(source_id=sid, external_id=fid, skipped_document=True)
                        )
                else:
                    would_change += 1
                    if dry_run:
                        report.stats.sources_processed += 1
                        sid = make_source_id(user_id=user_id, provider="google-drive", external_id=fid)
                        report.outcomes.append(
                            SyncRunOutcome(source_id=sid, external_id=fid, skipped_document=False)
                        )

            if dry_run:
                examined = would_change + would_skip
                print(
                    f"  google-drive ({src.external_id}): "
                    f"{examined} file(s) examined, {would_change} would process, {would_skip} unchanged (revision)"
                )
                continue

            assert model is not None
            for fid, meta in metas.items():
                if meta.get("trashed"):
                    continue
                if only_file_ids is not None and fid not in only_file_ids:
                    continue
                prev = state_store.get_source_record("google-drive", fid, user_id=user_id)
                rev = str(meta.get("headRevisionId") or "")
                etag = str(meta.get("etag") or "")
                sid = make_source_id(user_id=user_id, provider="google-drive", external_id=fid)

                if _drive_revision_unchanged(prev, rev, etag):
                    report.outcomes.append(
                        SyncRunOutcome(source_id=sid, external_id=fid, skipped_document=True)
                    )
                    report.stats.documents_skipped += 1
                    continue

                res = provider.import_documents(file_ids=[fid])
                if not res.documents:
                    continue
                doc = res.documents[0]
                raw_hash = sha256_bytes(doc.data)
                if should_skip_document_by_stored_hash(prev, raw_hash):
                    report.outcomes.append(
                        SyncRunOutcome(source_id=sid, external_id=fid, skipped_document=True)
                    )
                    report.stats.documents_skipped += 1
                    continue

                text = extract_text_from_bytes(doc.data, format=doc.format)
                text = normalize_unicode(text)
                text = optional_drop_metadata_lines(text, enabled=settings.skip_lines_filter)

                _run_llm_pipeline_for_source(
                    provider_key="google-drive",
                    external_id=fid,
                    revision_id=doc.revision_id or rev,
                    etag=doc.etag or etag,
                    content_sha256=raw_hash,
                    normalized_text=text,
                    settings=settings,
                    state_store=state_store,
                    model=model,
                    user_id=user_id,
                    chunk_cards=chunk_cards,
                    report=report,
                )

    if not dry_run:
        chunk_units_this_run = report.stats.chunks_processed + report.stats.chunks_skipped
        for exp in exporters:
            if not isinstance(exp, FileTargetExporter):
                raise TypeError(
                    "run_incremental_sync requires FileTargetExporter (with output_path); "
                    f"got {type(exp).__name__}"
                )
            rows = list(state_store.iter_all_cards(user_id=user_id))
            vrows = card_records_to_pipeline_rows(rows)
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
    if not dry_run:
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
