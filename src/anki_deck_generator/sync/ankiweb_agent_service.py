"""Cloud-side AnkiWeb pull-agent batching, cursor, and ack logic."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from anki_deck_generator.errors import StateError
from anki_deck_generator.export.ankiweb.exporter import (
    build_note_payload,
    card_to_anki_fields,
)
from anki_deck_generator.state.agent_tokens import generate_agent_token, hash_agent_token, verify_agent_token
from anki_deck_generator.state.records import (
    AgentRecord,
    CardRecord,
    IssuedBatchRecord,
    PendingSyncCursor,
    record_to_jsonable,
)
from anki_deck_generator.state.store import StateStore
from anki_deck_generator.sync.report import AnkiWebExportReport, SyncReport


def encode_cursor(cursor_at: datetime | None, cursor_card_id: str) -> str:
    if cursor_at is None:
        return ""
    return f"{cursor_at.isoformat()}|{cursor_card_id}"


def decode_cursor(raw: str | None) -> tuple[datetime | None, str]:
    if not raw:
        return None, ""
    if "|" not in raw:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")), ""
    ts_s, card_id = raw.split("|", 1)
    return datetime.fromisoformat(ts_s.replace("Z", "+00:00")), card_id


def card_needs_ankiweb_sync(card: CardRecord) -> bool:
    if card.last_updated_at is None:
        return False
    if card.ankiweb_last_synced_at is None:
        return True
    return card.ankiweb_last_synced_at < card.last_updated_at


def card_after_cursor(card: CardRecord, cursor_at: datetime | None, cursor_card_id: str) -> bool:
    if cursor_at is None:
        return True
    updated = card.last_updated_at
    if updated is None:
        return False
    if updated > cursor_at:
        return True
    if updated == cursor_at and card.card_id > cursor_card_id:
        return True
    return False


def select_pending_cards(
    cards: list[CardRecord],
    *,
    cursor_at: datetime | None,
    cursor_card_id: str,
    limit: int,
) -> list[CardRecord]:
    pending = [
        c
        for c in cards
        if card_needs_ankiweb_sync(c) and card_after_cursor(c, cursor_at, cursor_card_id)
    ]
    pending.sort(key=lambda c: (c.last_updated_at or datetime.min.replace(tzinfo=UTC), c.card_id))
    return pending[:limit]


def infer_op(card: CardRecord) -> str:
    if card.ankiweb_note_id is None:
        return "create"
    return "update"


@dataclass
class PendingBatchItem:
    op: str
    card_id: str
    anki: dict[str, Any]
    base_fields: dict[str, str] | None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "card_id": self.card_id,
            "anki": self.anki,
            "base_fields": self.base_fields,
        }


@dataclass
class PendingBatchResult:
    cursor: str
    batch_id: str
    items: list[PendingBatchItem] = field(default_factory=list)


@dataclass
class AckItemResult:
    card_id: str
    op: str
    status: str
    anki_note_id: int | None = None
    error: str | None = None
    conflict: dict[str, Any] | None = None
    applied_fields: dict[str, str] | None = None


@dataclass
class AckSummary:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    conflicts: int = 0
    errors: int = 0


def build_batch_items(
    cards: list[CardRecord],
    *,
    deck_name: str,
    model_name: str,
    req_id: str,
    run_date: str,
) -> list[PendingBatchItem]:
    items: list[PendingBatchItem] = []
    for card in cards:
        note = build_note_payload(
            card,
            deck_name=deck_name,
            model_name=model_name,
            req_id=req_id,
            run_date=run_date,
        )
        items.append(
            PendingBatchItem(
                op=infer_op(card),
                card_id=card.card_id,
                anki=note,
                base_fields=card.ankiweb_last_synced_fields,
            )
        )
    return items


def register_agent(
    store: StateStore,
    *,
    user_id: str,
    agent_id: str,
) -> tuple[AgentRecord, str]:
    now = datetime.now(UTC)
    existing = store.get_agent(agent_id, user_id=user_id)
    token = generate_agent_token()
    token_hash = hash_agent_token(token)
    rec = AgentRecord(
        agent_id=agent_id,
        token_hash=token_hash,
        created_at=existing.created_at if existing else now,
        last_seen_at=now,
        revoked_at=None,
        user_id=user_id,
    )
    store.upsert_agent(rec)
    return rec, token


def authenticate_agent(store: StateStore, *, user_id: str, agent_id: str, token: str) -> AgentRecord:
    rec = store.get_agent(agent_id, user_id=user_id)
    if rec is None or rec.revoked_at is not None:
        raise StateError("Invalid or revoked agent")
    if not verify_agent_token(token, rec.token_hash):
        raise StateError("Invalid agent token")
    return rec


def issue_pending_batch(
    store: StateStore,
    *,
    user_id: str,
    agent_id: str,
    cursor_raw: str | None,
    limit: int,
    deck_name: str,
    model_name: str,
) -> PendingBatchResult:
    open_batch = store.get_open_batch_for_agent(agent_id, user_id=user_id)
    if open_batch is not None:
        items = json.loads(open_batch.items_json or "[]")
        cursor = encode_cursor(open_batch.cursor_at, open_batch.cursor_card_id)
        return PendingBatchResult(cursor=cursor, batch_id=open_batch.batch_id, items=_items_from_json(items))

    stored_cursor = store.get_agent_cursor(agent_id, user_id=user_id)
    cursor_at = stored_cursor.cursor_at if stored_cursor else None
    cursor_card_id = stored_cursor.cursor_card_id if stored_cursor else ""
    if cursor_raw:
        parsed_at, parsed_id = decode_cursor(cursor_raw)
        if parsed_at is not None:
            cursor_at, cursor_card_id = parsed_at, parsed_id

    all_cards = list(store.iter_all_cards(user_id=user_id))
    selected = select_pending_cards(
        all_cards,
        cursor_at=cursor_at,
        cursor_card_id=cursor_card_id,
        limit=limit,
    )
    next_cursor = encode_cursor(cursor_at, cursor_card_id)
    if not selected:
        return PendingBatchResult(cursor=next_cursor, batch_id="", items=[])

    batch_id = str(uuid.uuid4())
    req_id = batch_id
    run_date = datetime.now(UTC).date().isoformat()
    items = build_batch_items(
        selected,
        deck_name=deck_name,
        model_name=model_name,
        req_id=req_id,
        run_date=run_date,
    )
    last = selected[-1]
    batch_rec = IssuedBatchRecord(
        batch_id=batch_id,
        agent_id=agent_id,
        user_id=user_id,
        issued_at=datetime.now(UTC),
        cursor_at=last.last_updated_at,
        cursor_card_id=last.card_id,
        items_json=json.dumps([i.to_jsonable() for i in items], sort_keys=True),
    )
    store.put_issued_batch(batch_rec)
    store.touch_agent_poll(agent_id, user_id=user_id, batch_id=batch_id)
    return PendingBatchResult(
        cursor=encode_cursor(last.last_updated_at, last.card_id),
        batch_id=batch_id,
        items=items,
    )


def _items_from_json(raw: list[dict[str, Any]]) -> list[PendingBatchItem]:
    out: list[PendingBatchItem] = []
    for row in raw:
        out.append(
            PendingBatchItem(
                op=str(row.get("op", "create")),
                card_id=str(row["card_id"]),
                anki=dict(row.get("anki", {})),
                base_fields=row.get("base_fields"),
            )
        )
    return out


def ack_batch(
    store: StateStore,
    *,
    user_id: str,
    agent_id: str,
    batch_id: str,
    results: list[AckItemResult],
    sync_requested: bool,
    sync_status: str,
    duration_ms: int = 0,
) -> AckSummary:
    batch = store.get_issued_batch(batch_id)
    if batch is None or batch.agent_id != agent_id or batch.user_id != user_id:
        raise StateError("Unknown or mismatched batch_id")
    if batch.acked_at is not None:
        raise StateError("Batch already acknowledged")

    summary = AckSummary()
    now = datetime.now(UTC)
    for item in results:
        if item.status == "applied":
            card = store.get_card_by_id(item.card_id)
            if card is None:
                summary.errors += 1
                continue
            fields = item.applied_fields or card_to_anki_fields(card)
            updated = replace(
                card,
                ankiweb_note_id=item.anki_note_id,
                ankiweb_last_synced_at=now,
                ankiweb_last_synced_fields=dict(fields),
            )
            store.upsert_card(updated)
            if item.op == "create":
                summary.created += 1
            else:
                summary.updated += 1
        elif item.status == "unchanged":
            summary.unchanged += 1
        elif item.status == "skipped":
            summary.skipped += 1
        elif item.status == "conflict":
            summary.conflicts += 1
        else:
            summary.errors += 1

    store.mark_batch_acked(batch_id, acked_at=now)
    if batch.cursor_at is not None:
        store.set_agent_cursor(
            PendingSyncCursor(
                agent_id=agent_id,
                cursor_at=batch.cursor_at,
                cursor_card_id=batch.cursor_card_id,
                user_id=user_id,
            )
        )
    store.touch_agent_poll(
        agent_id,
        user_id=user_id,
        batch_id=batch_id,
        sync_status=sync_status,
        seen_at=now,
    )

    export_report = AnkiWebExportReport(
        agent_id=agent_id,
        batch_id=batch_id,
        created=summary.created,
        updated=summary.updated,
        unchanged=summary.unchanged,
        skipped=summary.skipped,
        conflicts=summary.conflicts,
        errors=summary.errors,
        sync_requested=sync_requested,
        sync_status=sync_status,
        duration_ms=duration_ms,
    )
    _append_export_to_latest_run(store, user_id=user_id, export_report=export_report)
    return summary


def _append_export_to_latest_run(
    store: StateStore,
    *,
    user_id: str,
    export_report: AnkiWebExportReport,
) -> None:
    runs = list(store.iter_runs(limit=1))
    if not runs:
        return
    run = runs[0]
    if run.user_id != user_id:
        return
    try:
        data = json.loads(run.sync_report_json or "{}")
    except json.JSONDecodeError:
        data = {}
    exports = data.setdefault("exports", {})
    ankiweb_exports = exports.setdefault("ankiweb", [])
    ankiweb_exports.append(record_to_jsonable(asdict(export_report)))
    store.update_run_report(run.run_id, json.dumps(data, sort_keys=True), user_id=user_id)


def count_pending_cards(store: StateStore, *, user_id: str = "default") -> int:
    return sum(1 for c in store.iter_all_cards(user_id=user_id) if card_needs_ankiweb_sync(c))


def stale_agents(store: StateStore, *, user_id: str = "default", max_age_hours: int = 48) -> list[AgentRecord]:
    cutoff = datetime.now(UTC).timestamp() - max_age_hours * 3600
    stale: list[AgentRecord] = []
    for agent in store.iter_agents(user_id=user_id):
        if agent.revoked_at is not None:
            continue
        seen = agent.last_poll_at or agent.last_seen_at
        if seen is None or seen.timestamp() < cutoff:
            stale.append(agent)
    return stale
