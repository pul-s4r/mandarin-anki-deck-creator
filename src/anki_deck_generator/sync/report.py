"""Incremental sync reporting."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class SyncRunOutcome:
    source_id: str
    external_id: str
    skipped_document: bool = False
    cards_created: int = 0
    cards_updated: int = 0
    cards_unchanged: int = 0


@dataclass
class SyncReportStats:
    chunks_processed: int = 0
    chunks_skipped: int = 0
    documents_skipped: int = 0
    sources_processed: int = 0


@dataclass
class SyncReport:
    run_id: str
    run_started_at: datetime
    run_finished_at: datetime | None
    outcomes: list[SyncRunOutcome] = field(default_factory=list)
    stats: SyncReportStats = field(default_factory=SyncReportStats)
    export_paths: list[str] = field(default_factory=list)
    dry_run: bool = False

    def to_jsonable(self) -> dict:
        def _dt(o: object) -> object:
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, dict):
                return {k: _dt(v) for k, v in o.items()}
            if isinstance(o, list):
                return [_dt(v) for v in o]
            return o

        return _dt(asdict(self))

    def to_json(self) -> str:
        return json.dumps(self.to_jsonable(), sort_keys=True)
