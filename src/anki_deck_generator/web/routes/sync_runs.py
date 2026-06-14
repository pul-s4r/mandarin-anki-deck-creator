"""Sync run status API."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from anki_deck_generator.state.store import StateStore
from anki_deck_generator.web.dependencies import get_state_store
from anki_deck_generator.web.schemas import AnkiWebExportBlock, SyncRunDetailResponse

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/runs/{run_id}", response_model=SyncRunDetailResponse)
def get_sync_run(
    run_id: str,
    store: Annotated[StateStore, Depends(get_state_store)],
) -> SyncRunDetailResponse:
    rec = store.get_run(run_id, user_id="default")
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    try:
        report = json.loads(rec.sync_report_json or "{}")
    except json.JSONDecodeError:
        report = {}
    exports_raw = report.get("exports", {}).get("ankiweb", [])
    exports = [AnkiWebExportBlock.model_validate(row) for row in exports_raw if isinstance(row, dict)]
    return SyncRunDetailResponse(
        run_id=rec.run_id,
        trigger=rec.trigger,
        started_at=rec.started_at.isoformat() if rec.started_at else None,
        finished_at=rec.finished_at.isoformat() if rec.finished_at else None,
        sync_report=report,
        exports_ankiweb=exports,
    )
