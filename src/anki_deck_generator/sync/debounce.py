"""Debounce logic for Drive pending-edit processing (D7).

Provides helpers to compute ``ready_at`` / ``hard_deadline_at`` and to
determine the reason a pending edit is ready.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anki_deck_generator.state.records import PendingEditRecord


class ReadyReason(StrEnum):
    QUIET_ELAPSED = "quiet_elapsed"
    HARD_DEADLINE = "hard_deadline"
    FORCE = "force"


def pending_edit_ready_reason(
    rec: PendingEditRecord,
    *,
    now: datetime,
) -> ReadyReason | None:
    """Return the reason this edit is ready (or None if it should wait)."""
    if rec.force_process:
        return ReadyReason.FORCE
    if rec.hard_deadline_at is not None and rec.hard_deadline_at <= now:
        return ReadyReason.HARD_DEADLINE
    if rec.ready_at is not None and rec.ready_at <= now:
        return ReadyReason.QUIET_ELAPSED
    return None


def is_pending_edit_ready(rec: PendingEditRecord, *, now: datetime) -> bool:
    return pending_edit_ready_reason(rec, now=now) is not None
