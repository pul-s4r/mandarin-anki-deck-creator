"""Unified sync Lambda handler (two modes).

Mode A — ``pull_changes``
    Called when a Drive change notification has been received (via SQS from the
    webhook handler).  Pulls ``changes.list`` from Drive, upserts PendingEdits
    for each in-scope file, then advances ``pageToken``.  This is the **only**
    code path that may advance the cursor.

Mode B — ``process_pending``
    Called on a 1-minute EventBridge tick (or manually).  Scans PendingEdits for
    rows whose ``ready_at`` or ``hard_deadline_at`` has passed (or ``force``),
    calls ``run_sync`` for each batch of ready files, then clears the processed
    rows — but only when no new events arrived during processing.

Single-cursor-owner invariant
    Only mode A advances ``DriveChannel.page_token``.
    Mode B never mutates ``page_token``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from anki_deck_generator.state.records import PendingEditRecord
from anki_deck_generator.state.store import StateStore

if TYPE_CHECKING:
    from anki_deck_generator.config.settings import Settings
    from anki_deck_generator.config.source_sets import SourceSet
    from anki_deck_generator.export.base import Exporter
    from anki_deck_generator.sync.report import SyncReport

logger = logging.getLogger(__name__)


class DriveChangesClient(Protocol):
    """Minimal interface to the Drive changes API (mockable for tests)."""

    def list_changes(self, page_token: str) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Mode A — pull changes + upsert PendingEdits + advance pageToken
# ---------------------------------------------------------------------------


def pull_changes(
    *,
    channel_id: str,
    state_store: StateStore,
    drive_client: DriveChangesClient,
    source_set_name: str,
    quiet_minutes: int = 10,
    max_delay_minutes: int = 120,
    file_filter: Any | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    """Mode A: pull changes.list, upsert PendingEdits, advance pageToken.

    *file_filter* is an optional ``(file_meta: dict) -> bool`` predicate that
    decides whether a changed file is in scope for this source set (e.g. MIME
    type check + ancestor-folder check).  When ``None``, all non-removed/trashed
    files pass.

    Returns a summary dict suitable for structured logging.
    """
    channel = state_store.get_drive_channel(channel_id)
    if channel is None:
        raise ValueError(f"Unknown channel: {channel_id}")

    page_token = channel.page_token
    if not page_token:
        raise ValueError(f"Channel {channel_id} has no page_token")

    collected_file_ids: set[str] = set()
    current_token = page_token

    while True:
        resp = drive_client.list_changes(current_token)
        changes = resp.get("changes", [])

        for change in changes:
            if change.get("removed"):
                continue
            f = change.get("file", {})
            if f.get("trashed"):
                continue
            if file_filter is not None and not file_filter(f):
                continue
            collected_file_ids.add(change.get("fileId") or f.get("id", ""))

        if "nextPageToken" in resp:
            current_token = resp["nextPageToken"]
            continue

        new_token = resp.get("newStartPageToken", current_token)
        break

    now = datetime.now(UTC)
    for fid in collected_file_ids:
        if not fid:
            continue
        state_store.upsert_pending_edit(
            PendingEditRecord(
                source_set=source_set_name,
                file_id=fid,
                provider="google-drive",
                first_seen_at=now,
                last_seen_at=now,
                ready_at=now + timedelta(minutes=quiet_minutes),
                hard_deadline_at=now + timedelta(minutes=max_delay_minutes),
                message_count=1,
                user_id=user_id,
            )
        )

    advanced = state_store.advance_drive_channel_token(
        channel_id,
        expected_prev_token=page_token,
        new_token=new_token,
    )

    summary = {
        "event": "drive.changes.pulled",
        "channel_id": channel_id,
        "source_set": source_set_name,
        "changes_seen": len(collected_file_ids),
        "page_token_advanced": advanced,
        "new_token": new_token if advanced else "(not advanced)",
    }
    logger.info("%s", summary)
    return summary


# ---------------------------------------------------------------------------
# Mode B — poll ready PendingEdits + run_sync + clear
# ---------------------------------------------------------------------------


def process_pending(
    *,
    state_store: StateStore,
    settings: Any,
    source_set: Any,
    exporters: list[Any] | None = None,
    source_set_name: str | None = None,
    user_id: str = "default",
    run_sync_fn: Any | None = None,
) -> dict[str, Any]:
    """Mode B: poll ready PendingEdits, run run_sync, clear processed rows.

    *run_sync_fn* is injected for testability; defaults to
    ``anki_deck_generator.sync.orchestrator.run_incremental_sync``.

    Returns a summary dict suitable for structured logging.  Mode B **never**
    advances ``page_token``.
    """
    if run_sync_fn is None:
        from anki_deck_generator.sync.orchestrator import run_incremental_sync
        run_sync_fn = run_incremental_sync

    now = datetime.now(UTC)
    run_started_at = now

    ss_name = source_set_name or getattr(source_set, "name", "unknown")
    ready = list(
        state_store.iter_ready_pending_edits(now, source_set=ss_name)
    )

    if not ready:
        return {
            "event": "pending.process",
            "source_set": ss_name,
            "files_ready": 0,
            "files_processed": 0,
        }

    file_ids = [r.file_id for r in ready]

    report = run_sync_fn(
        source_set,
        settings=settings,
        state_store=state_store,
        exporters=exporters or [],
        only_file_ids=file_ids,
        user_id=user_id,
    )

    cleared = 0
    for r in ready:
        if state_store.clear_pending_edit(
            r.source_set,
            r.file_id,
            if_last_seen_before=run_started_at,
        ):
            cleared += 1

    summary = {
        "event": "pending.processed",
        "source_set": ss_name,
        "files_ready": len(ready),
        "files_processed": len(file_ids),
        "files_cleared": cleared,
        "files_retained": len(ready) - cleared,
    }
    logger.info("%s", summary)
    return summary
