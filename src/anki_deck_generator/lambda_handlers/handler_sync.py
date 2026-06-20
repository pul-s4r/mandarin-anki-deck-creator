"""Lambda handler: incremental sync (thin wrapper for Mode B / scheduled sync).

Delegates to the shared ``process_pending`` or ``run_incremental_sync`` service.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def handler(event: dict, context: object) -> dict:
    """AWS Lambda entry point for scheduled or event-driven sync."""
    trigger = event.get("trigger", "schedule")
    source_set_name = event.get("source_set_name") or os.environ.get("ANKI_SOURCE_SET_NAME", "")
    user_id = event.get("user_id", "default")

    if not source_set_name:
        return {"statusCode": 400, "body": "Missing source_set_name"}

    logger.info("Lambda sync: trigger=%r source_set=%r", trigger, source_set_name)

    try:
        count = _run(
            trigger=trigger,
            source_set_name=source_set_name,
            user_id=user_id,
        )
        return {"statusCode": 200, "body": json.dumps({"processed": count})}
    except Exception as exc:
        logger.error("Lambda sync error: %s", exc)
        return {"statusCode": 500, "body": str(exc)}


def _run(*, trigger: str, source_set_name: str, user_id: str) -> int:
    from anki_deck_generator.config.settings import Settings
    from anki_deck_generator.sync.drive_events import process_pending
    from anki_deck_generator.state.sqlite_store import SqliteStateStore

    db_path = os.environ.get("ANKI_STATE_DB_PATH", "/tmp/state.db")
    store = SqliteStateStore(Path(db_path))
    store.init_schema()

    settings = Settings()
    return process_pending(
        state_store=store,
        source_set_name=source_set_name,
        settings=settings,
        exporters=[],
        user_id=user_id,
    )
