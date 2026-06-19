"""Persist pipeline results from HTTP file uploads into StateStore."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from anki_deck_generator.pipeline_types import PipelineResult
from anki_deck_generator.preprocess.fingerprints import sha256_bytes
from anki_deck_generator.state.records import CardUpsertResult, RunReportRecord, SourceRecord
from anki_deck_generator.state.store import StateStore
from anki_deck_generator.sync.cards_bridge import vocabulary_row_to_card_record
from anki_deck_generator.sync.report import SyncReport, SyncReportStats, SyncRunOutcome
from anki_deck_generator.sync.source_ids import make_source_id

_API_UPLOAD_PROVIDER = "api-upload"


@dataclass(frozen=True)
class UploadPersistenceResult:
    run_id: str
    source_id: str
    cards_created: int
    cards_updated: int
    cards_unchanged: int


def persist_api_upload(
    store: StateStore,
    *,
    filename: str,
    raw_bytes: bytes,
    pipeline_result: PipelineResult,
    user_id: str = "default",
) -> UploadPersistenceResult:
    """Upsert vocabulary rows and record a sync run for an uploaded file."""
    started = datetime.now(UTC)
    run_id = str(uuid.uuid4())
    content_sha256 = sha256_bytes(raw_bytes)
    external_id = f"{filename}:{content_sha256}"
    source_id = make_source_id(user_id=user_id, provider=_API_UPLOAD_PROVIDER, external_id=external_id)

    cards_created = 0
    cards_updated = 0
    cards_unchanged = 0
    for row in pipeline_result.rows:
        existing = store.get_card_by_key(row.simplified.strip(), user_id=user_id)
        rec = vocabulary_row_to_card_record(row, source_id=source_id, user_id=user_id, existing=existing)
        upsert = store.upsert_card(rec)
        if upsert is CardUpsertResult.CREATED:
            cards_created += 1
        elif upsert is CardUpsertResult.UPDATED:
            cards_updated += 1
        else:
            cards_unchanged += 1

    finished = datetime.now(UTC)
    store.upsert_source_record(
        SourceRecord(
            source_id=source_id,
            provider=_API_UPLOAD_PROVIDER,
            external_id=external_id,
            content_sha256=content_sha256,
            last_ingested_at=finished,
            user_id=user_id,
        )
    )

    outcome = SyncRunOutcome(
        source_id=source_id,
        external_id=external_id,
        skipped_document=False,
        cards_created=cards_created,
        cards_updated=cards_updated,
        cards_unchanged=cards_unchanged,
    )
    report = SyncReport(
        run_id=run_id,
        run_started_at=started,
        run_finished_at=finished,
        outcomes=[outcome],
        stats=SyncReportStats(
            sources_processed=1,
            chunks_processed=pipeline_result.stats.chunk_count,
        ),
    )
    store.record_run(
        RunReportRecord(
            run_id=run_id,
            trigger="api-upload",
            started_at=started,
            finished_at=finished,
            sync_report_json=report.to_json(),
            user_id=user_id,
        )
    )
    return UploadPersistenceResult(
        run_id=run_id,
        source_id=source_id,
        cards_created=cards_created,
        cards_updated=cards_updated,
        cards_unchanged=cards_unchanged,
    )
