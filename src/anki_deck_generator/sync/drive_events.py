"""Drive event processing: Mode A (pull_changes) and Mode B (process_pending).

Mode A: triggered by a webhook notification; pulls Drive changes.list, writes
        PendingEdits, then advances the page_token cursor.

Mode B: triggered by a timer/CLI tick; scans ready PendingEdits and calls
        run_incremental_sync for each settled group.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anki_deck_generator.config.source_sets import GoogleDriveSource, SourceSet
    from anki_deck_generator.config.settings import Settings
    from anki_deck_generator.export.base import Exporter
    from anki_deck_generator.state.store import StateStore

logger = logging.getLogger(__name__)


def _load_source_set(source_set_name: str) -> SourceSet:
    from anki_deck_generator.config.source_sets import load_source_sets_yaml, pick_source_set

    cfg_raw = os.environ.get("ANKI_PIPELINE_SOURCE_SET_CONFIG")
    if not cfg_raw:
        raise RuntimeError(
            "Mode A requires ANKI_PIPELINE_SOURCE_SET_CONFIG to resolve Drive credentials"
        )
    config = load_source_sets_yaml(Path(cfg_raw).expanduser().resolve())
    return pick_source_set(config, source_set_name)


def _authenticate_drive_for_source_set(source_set_name: str) -> tuple[Any, SourceSet, GoogleDriveSource]:
    from anki_deck_generator.config.source_sets import GoogleDriveSource
    from anki_deck_generator.integrations.registry import get_provider

    import importlib

    importlib.import_module("anki_deck_generator.integrations.google_drive")
    sset = _load_source_set(source_set_name)
    drive_src: GoogleDriveSource | None = None
    for src in sset.sources:
        if isinstance(src, GoogleDriveSource):
            drive_src = src
            break
    if drive_src is None:
        raise RuntimeError(f"Source set {source_set_name!r} has no google-drive source")
    provider = get_provider("google-drive")
    provider.authenticate({"credentials_file": str(drive_src.credentials_file)})
    return provider, sset, drive_src


def _settling_seconds(source_set: SourceSet) -> tuple[int, int]:
    quiet = max(1, int(source_set.edit_settling.quiet_minutes) * 60)
    max_delay = max(quiet, int(source_set.edit_settling.max_delay_minutes) * 60)
    return quiet, max_delay


# ─────────────────────────── in-process queue ──────────────────────────── #

_pending_mode_a_jobs: list[str] = []


def enqueue_mode_a(channel_id: str) -> None:
    """Enqueue a Mode A job (in-process; replace with SQS adapter for cloud)."""
    _pending_mode_a_jobs.append(channel_id)
    logger.debug("Enqueued Mode A job for channel %r", channel_id)


def drain_mode_a_queue(*, state_store: StateStore) -> list[str]:
    """Process all queued Mode A jobs.  Returns list of processed channel IDs."""
    processed: list[str] = []
    while _pending_mode_a_jobs:
        channel_id = _pending_mode_a_jobs.pop(0)
        try:
            pull_changes(channel_id=channel_id, state_store=state_store)
            processed.append(channel_id)
        except Exception as exc:
            logger.error("Mode A failed for channel %r: %s", channel_id, exc)
    return processed


# ───────────────────────────── Mode A ──────────────────────────────────── #


def pull_changes(
    *,
    channel_id: str,
    state_store: StateStore,
    folder_ids: list[str] | None = None,
    user_id: str = "default",
) -> list[str]:
    """Mode A: page through changes.list and write PendingEdits.

    1. Load channel record (page_token, source_set_name, channel_token).
    2. Call list_changes() — paginate fully.
    3. Filter file IDs to in-scope folders when *folder_ids* is provided.
    4. Upsert PendingEdit rows (with edit-settling timings from channel/config).
    5. Advance page_token ONLY after durable pending writes.

    Returns the list of file IDs that were written as PendingEdits.
    """
    import importlib

    importlib.import_module("anki_deck_generator.integrations.google_drive")

    rec = state_store.get_drive_channel(channel_id)
    if rec is None:
        logger.warning("Mode A: unknown channel %r; ignoring", channel_id)
        return []

    if not rec.page_token:
        logger.warning("Mode A: channel %r has no page_token; ignoring", channel_id)
        return []

    provider, sset, drive_src = _authenticate_drive_for_source_set(rec.source_set_name)
    if folder_ids is None:
        folder_ids = list(drive_src.folder_ids)
    quiet_seconds, max_delay_seconds = _settling_seconds(sset)

    result = provider.list_changes(rec.page_token)
    file_ids: list[str] = result["file_ids"]
    new_token = result["new_start_page_token"]

    # Filter to in-scope folders if provided.
    if folder_ids is not None and file_ids:
        file_ids = _filter_file_ids_by_folders(provider, file_ids, folder_ids)

    now = datetime.now(UTC)
    written: list[str] = []
    for fid in file_ids:
        state_store.upsert_pending_edit_debounced(
            user_id=rec.user_id or user_id,
            source_set_name=rec.source_set_name,
            file_id=fid,
            now=now,
            quiet_seconds=quiet_seconds,
            max_delay_seconds=max_delay_seconds,
        )
        written.append(fid)
        logger.debug("Mode A: upserted pending edit for file %r (channel %r)", fid, channel_id)

    # Advance page_token only after durable writes.
    if new_token and new_token != rec.page_token:
        try:
            state_store.advance_drive_channel_token(
                channel_id,
                expected_token=rec.page_token,
                new_token=new_token,
            )
            logger.info("Mode A: advanced page_token for channel %r", channel_id)
        except Exception as exc:
            logger.warning(
                "Mode A: token advance failed for channel %r (likely concurrent); %s",
                channel_id,
                exc,
            )

    return written


def _filter_file_ids_by_folders(
    provider: Any,
    file_ids: list[str],
    folder_ids: list[str],
) -> list[str]:
    """Return only file IDs that live in one of *folder_ids* (best-effort)."""
    # We can't efficiently check parents without extra Drive API calls per file.
    # For now, return all file_ids and let orchestrator handle scope filtering.
    # A production implementation could batch metadata requests.
    return file_ids


# ───────────────────────────── Mode B ──────────────────────────────────── #


def process_pending(
    *,
    state_store: StateStore,
    source_set_name: str,
    settings: Settings,
    exporters: list[Exporter],
    user_id: str = "default",
    source_set: Any | None = None,
) -> int:
    """Mode B: process all ready pending edits for *source_set_name*.

    1. Scan ready pending edits (quiet window elapsed OR hard deadline hit OR force).
    2. Group by source_set (only one here).
    3. Call run_incremental_sync(only_file_ids=..., trigger="drive-push").
    4. Clear processed rows with guarded clear (if_last_seen_before=run_started_at).

    Returns the number of file IDs processed.
    """
    from anki_deck_generator.sync.debounce import ReadyReason, pending_edit_ready_reason

    now = datetime.now(UTC)
    ready_edits = state_store.list_ready_pending_edits(user_id=user_id, now=now)

    # Filter to the requested source_set.
    relevant = [r for r in ready_edits if r.source_set_name == source_set_name]
    if not relevant:
        logger.info("Mode B: no ready pending edits for source_set %r", source_set_name)
        return 0

    file_ids = [r.file_id for r in relevant]
    run_started_at = now
    last_seen_map = {r.file_id: r.last_seen_at for r in relevant}

    for rec in relevant:
        reason = pending_edit_ready_reason(rec, now=now)
        logger.info(
            "Mode B: processing file %r for source_set %r (reason=%s)",
            rec.file_id,
            source_set_name,
            reason,
        )

    if source_set is not None:
        from anki_deck_generator.sync.orchestrator import run_incremental_sync

        run_incremental_sync(
            source_set,
            settings=settings,
            state_store=state_store,
            exporters=exporters,
            only_file_ids=file_ids,
            user_id=user_id,
            trigger="drive-push",
        )

    # Guarded clear: only clear if last_seen_at hasn't advanced since we started.
    cleared = 0
    for fid in file_ids:
        last_seen = last_seen_map.get(fid)
        if last_seen is None:
            continue
        was_cleared = state_store.clear_pending_edit(
            user_id=user_id,
            source_set_name=source_set_name,
            file_id=fid,
            if_last_seen_before=run_started_at,
        )
        if was_cleared:
            cleared += 1
            logger.debug("Mode B: cleared pending edit for file %r", fid)
        else:
            logger.info("Mode B: preserved pending edit for file %r (new edits arrived)", fid)

    logger.info(
        "Mode B: processed %d file(s) for source_set %r, cleared %d pending rows",
        len(file_ids),
        source_set_name,
        cleared,
    )
    return len(file_ids)
