"""Card → Anki note mapping and AnkiConnect export orchestration."""

from __future__ import annotations

import html
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any

from anki_deck_generator.errors import AnkiConnectError
from anki_deck_generator.export.ankiweb.anki_connect import AnkiConnectClient
from anki_deck_generator.export.ankiweb.merge import three_way_merge
from anki_deck_generator.state.records import CardRecord
from anki_deck_generator.state.store import StateStore

logger = logging.getLogger(__name__)

ANKI_MODEL_FIELDS: tuple[str, ...] = (
    "Simplified",
    "Traditional",
    "Pinyin",
    "Meaning",
    "PartOfSpeech",
    "UsageNotes",
    "SourceRef",
    "ExtId",
)

_VALID_CONFLICT_POLICIES: frozenset[str] = frozenset({"prefer-remote", "prefer-local", "tag-and-skip"})

_MANAGED_TAG_PREFIXES: tuple[str, ...] = ("ext_id:", "req:", "run:", "src:", "enr:", "conflict:")


@dataclass
class ConflictRecord:
    card_id: str
    fields: list[str]
    chosen: str


@dataclass
class AnkiWebExportResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    conflicts: list[ConflictRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    sync_requested: bool = False
    sync_status: str = ""


def _html_escape_multiline(text: str) -> str:
    return html.escape(text or "", quote=False).replace("\n", "<br>")


def card_to_anki_fields(card: CardRecord) -> dict[str, str]:
    """Map persistent card fields to Anki model field names (§13.3.9)."""
    return {
        "Simplified": card.simplified or "",
        "Traditional": card.traditional or "",
        "Pinyin": card.pinyin or "",
        "Meaning": _html_escape_multiline(card.meaning),
        "PartOfSpeech": card.part_of_speech or "",
        "UsageNotes": _html_escape_multiline(card.usage_notes),
        "SourceRef": card.first_seen_source_id or "",
        "ExtId": card.card_id,
    }


def card_to_anki_tags(card: CardRecord, *, req_id: str, run_date: str) -> list[str]:
    tags = [f"ext_id:{card.card_id}", f"req:{req_id}"]
    rd = run_date.strip()
    if rd:
        tags.append(f"run:{rd}")
    src = (card.first_seen_source_id or "").strip()
    if src:
        tags.append(f"src:{src}")
    tags.append(f"enr:{card.schema_version}")
    return tags


def build_note_payload(
    card: CardRecord,
    *,
    deck_name: str,
    model_name: str,
    req_id: str,
    run_date: str,
) -> dict[str, Any]:
    """``addNotes`` / ``canAddNotesWithErrorDetail`` note dict."""
    fields = card_to_anki_fields(card)
    tags = card_to_anki_tags(card, req_id=req_id, run_date=run_date)
    return {
        "deckName": deck_name,
        "modelName": model_name,
        "fields": fields,
        "tags": tags,
        "options": {"allowDuplicate": False, "dupScope": "deck", "req_id": req_id},
    }


def _split_tags(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(t) for t in raw]
    if isinstance(raw, str):
        return [t for t in raw.replace(",", " ").split() if t]
    return []


def _merge_managed_tags(existing_tags: list[str], fresh_pipeline_tags: list[str]) -> list[str]:
    kept = [t for t in existing_tags if not any(t.startswith(p) for p in _MANAGED_TAG_PREFIXES)]
    return kept + fresh_pipeline_tags


def _flatten_note_fields(raw_fields: object) -> dict[str, str]:
    if not isinstance(raw_fields, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in raw_fields.items():
        k = str(key)
        if isinstance(val, dict) and "value" in val:
            out[k] = str(val.get("value") or "")
        else:
            out[k] = str(val or "")
    return out


def _default_model_spec(model_name: str) -> dict[str, Any]:
    back_inner = "<br>".join(f"{{{{{name}}}}}" for name in ANKI_MODEL_FIELDS if name != "Simplified")
    return {
        "modelName": model_name,
        "inOrderFields": list(ANKI_MODEL_FIELDS),
        "css": "",
        "isCloze": False,
        "cardTemplates": [{"Name": "Card 1", "Front": "{{Simplified}}", "Back": back_inner}],
    }


def _preflight(
    client: AnkiConnectClient,
    *,
    deck_name: str,
    model_name: str,
    auto_create_deck: bool,
    auto_create_model: bool,
) -> None:
    ver = client.version()
    if ver < 6:
        raise AnkiConnectError(f"AnkiConnect API version must be >= 6, got {ver}")
    perm = client.request_permission()
    if perm.get("permission") != "granted":
        raise AnkiConnectError(f"AnkiConnect permission not granted: {perm!r}")

    decks = set(client.deck_names())
    if deck_name not in decks:
        if not auto_create_deck:
            raise AnkiConnectError(
                f"Deck {deck_name!r} does not exist and auto_create_deck is false "
                "(create the deck in Anki or enable auto_create_deck)."
            )
        client.create_deck(deck_name)

    models = set(client.model_names())
    if model_name not in models:
        if not auto_create_model:
            raise AnkiConnectError(
                f"Model {model_name!r} does not exist and auto_create_model is false "
                "(import the model or enable auto_create_model)."
            )
        client.create_model(_default_model_spec(model_name))
        return None

    fields = client.model_field_names(model_name)
    if tuple(fields) != ANKI_MODEL_FIELDS:
        raise AnkiConnectError(
            f"Note type {model_name!r} must have fields {ANKI_MODEL_FIELDS!r} in order; "
            f"found {tuple(fields)!r}. Adjust the model or pick another model_name."
        )
    return None


def _resolve_note_id(client: AnkiConnectClient, card: CardRecord) -> int | None:
    tag_need = f"ext_id:{card.card_id}"
    if card.ankiweb_note_id is not None:
        infos = client.notes_info([card.ankiweb_note_id])
        if infos:
            entry = infos[0]
            tags = _split_tags(entry.get("tags"))
            if tag_need in tags:
                raw_id = entry.get("noteId") or card.ankiweb_note_id
                return int(raw_id)
    found = client.find_notes(f'tag:"ext_id:{card.card_id}"')
    if not found:
        return None
    if len(found) > 1:
        chosen = min(found)
        logger.warning(
            "Multiple Anki notes tagged %s; updating earliest note id %s",
            tag_need,
            chosen,
        )
        return int(chosen)
    return int(found[0])


def _persist_card_sync(
    state_store: StateStore,
    card: CardRecord,
    *,
    note_id: int,
    synced_fields: dict[str, str],
) -> None:
    now = datetime.now(UTC)
    updated = replace(
        card,
        ankiweb_note_id=note_id,
        ankiweb_last_synced_at=now,
        ankiweb_last_synced_fields=dict(synced_fields),
    )
    state_store.upsert_card(updated)


def _chosen_side(conflict_policy: str) -> str:
    if conflict_policy == "prefer-local":
        return "local"
    if conflict_policy in ("prefer-remote", "tag-and-skip"):
        return "remote"
    raise ValueError(
        f"conflict_policy must be one of {sorted(_VALID_CONFLICT_POLICIES)}, got {conflict_policy!r}"
    )


def _process_existing_note(
    *,
    client: AnkiConnectClient,
    state_store: StateStore,
    card: CardRecord,
    note_id: int,
    req_id: str,
    run_date: str,
    conflict_policy: str,
    result: AnkiWebExportResult,
) -> bool:
    """Returns True if the note was created or updated (collection dirty)."""
    infos = client.notes_info([note_id])
    if not infos:
        return False
    entry = infos[0]
    tags = _split_tags(entry.get("tags"))
    if f"req:{req_id}" in tags:
        result.unchanged += 1
        return False

    remote_flat = _flatten_note_fields(entry.get("fields"))
    local_flat = card_to_anki_fields(card)
    merge = three_way_merge(
        base_fields=card.ankiweb_last_synced_fields,
        remote_fields=remote_flat,
        local_fields=local_flat,
        conflict_policy=conflict_policy,
        field_names=ANKI_MODEL_FIELDS,
    )

    if merge.has_conflict:
        result.conflicts.append(
            ConflictRecord(
                card_id=card.card_id,
                fields=list(merge.conflicted_field_names),
                chosen=_chosen_side(conflict_policy),
            )
        )

    if conflict_policy == "tag-and-skip" and merge.has_conflict:
        client.add_tags(note_ids=[note_id], tags=f"conflict:{card.card_id}")
        result.skipped += 1
        return False

    if merge.has_update:
        fresh = card_to_anki_tags(card, req_id=req_id, run_date=run_date)
        new_tags = _merge_managed_tags(tags, fresh)
        client.update_note(
            note={
                "id": note_id,
                "fields": merge.merged_fields,
                "tags": new_tags,
            }
        )
        result.updated += 1
        _persist_card_sync(state_store, card, note_id=note_id, synced_fields=merge.merged_fields)
        return True

    result.unchanged += 1
    _persist_card_sync(state_store, card, note_id=note_id, synced_fields=remote_flat)
    return False


def _apply_create_or_duplicate(
    *,
    client: AnkiConnectClient,
    state_store: StateStore,
    card: CardRecord,
    payload: dict[str, Any],
    req_id: str,
    run_date: str,
    conflict_policy: str,
    result: AnkiWebExportResult,
) -> bool:
    """Returns True if the collection should sync to AnkiWeb."""
    checks = client.can_add_notes_with_error_detail([payload])
    chk = checks[0] if checks else {}
    can_add = bool(chk.get("canAdd"))
    err = str(chk.get("error") or "")
    if can_add:
        ids = client.add_notes([payload])
        nid_raw = ids[0] if ids else None
        if nid_raw is None:
            note_id = _resolve_note_id(client, card)
            if note_id is None:
                msg = f"addNotes returned null for card {card.card_id}; could not resolve note"
                logger.warning("Anki export error: %s", msg)
                result.errors.append(msg)
                return False
            return _process_existing_note(
                client=client,
                state_store=state_store,
                card=card,
                note_id=note_id,
                req_id=req_id,
                run_date=run_date,
                conflict_policy=conflict_policy,
                result=result,
            )
        note_id = int(nid_raw)
        result.created += 1
        _persist_card_sync(state_store, card, note_id=note_id, synced_fields=card_to_anki_fields(card))
        return True

    if "duplicate" not in err.lower():
        msg = f"cannot add note for card {card.card_id}: {err}"
        logger.warning("Anki export error: %s", msg)
        result.errors.append(msg)
        return False

    note_id = _resolve_note_id(client, card)
    if note_id is None:
        msg = f"duplicate note but no ext_id tag for card {card.card_id}"
        logger.warning("Anki export error: %s", msg)
        result.errors.append(msg)
        return False
    return _process_existing_note(
        client=client,
        state_store=state_store,
        card=card,
        note_id=note_id,
        req_id=req_id,
        run_date=run_date,
        conflict_policy=conflict_policy,
        result=result,
    )


def export_to_ankiweb(
    *,
    cards: Iterable[CardRecord],
    state_store: StateStore,
    client: AnkiConnectClient,
    deck_name: str,
    model_name: str = "Chinese vocabulary",
    conflict_policy: str = "prefer-remote",
    auto_create_deck: bool = True,
    auto_create_model: bool = True,
    batch_size: int = 50,
    run_date: str = "",
    user_id: str = "default",
    auto_sync: bool = True,
) -> AnkiWebExportResult:
    """Push vocabulary cards to desktop Anki via AnkiConnect (§13.3).

    ``batch_size`` is reserved for future batched ``addNotes``; exports are applied
    sequentially for correctness with duplicate and merge fallbacks.

    When ``auto_sync`` is false, mutations are not pushed to AnkiWeb via
    ``client.sync()``; the caller may sync manually.
    """
    _ = batch_size
    if conflict_policy not in _VALID_CONFLICT_POLICIES:
        raise ValueError(
            f"conflict_policy must be one of {sorted(_VALID_CONFLICT_POLICIES)}, got {conflict_policy!r}"
        )
    rd = run_date.strip() or date.today().isoformat()
    req_id = str(uuid.uuid4())
    result = AnkiWebExportResult()
    cards_list = [c for c in cards if c.user_id == user_id]
    _preflight(
        client,
        deck_name=deck_name,
        model_name=model_name,
        auto_create_deck=auto_create_deck,
        auto_create_model=auto_create_model,
    )

    needs_sync = False
    pending_create: list[CardRecord] = []

    for card in cards_list:
        nid = _resolve_note_id(client, card)
        if nid is None:
            pending_create.append(card)
            continue
        try:
            if _process_existing_note(
                client=client,
                state_store=state_store,
                card=card,
                note_id=nid,
                req_id=req_id,
                run_date=rd,
                conflict_policy=conflict_policy,
                result=result,
            ):
                needs_sync = True
        except AnkiConnectError as exc:
            logger.warning("Anki export error for card %s: %s", card.card_id, exc)
            result.errors.append(str(exc))

    for card in pending_create:
        payload = build_note_payload(
            card,
            deck_name=deck_name,
            model_name=model_name,
            req_id=req_id,
            run_date=rd,
        )
        try:
            if _apply_create_or_duplicate(
                client=client,
                state_store=state_store,
                card=card,
                payload=payload,
                req_id=req_id,
                run_date=rd,
                conflict_policy=conflict_policy,
                result=result,
            ):
                needs_sync = True
        except AnkiConnectError as exc:
            logger.warning("Anki export error for card %s: %s", card.card_id, exc)
            result.errors.append(str(exc))

    if needs_sync:
        result.sync_requested = True
        if not auto_sync:
            return result
        try:
            client.sync()
            result.sync_status = "ok"
        except AnkiConnectError as exc:
            result.sync_status = f"failed: {exc}"
            logger.warning("AnkiConnect sync failed after export: %s", exc)

    if result.errors:
        logger.warning("Anki export had %d errors:", len(result.errors))
        for err in result.errors[:10]:
            logger.warning("  - %s", err)
        if len(result.errors) > 10:
            logger.warning("  ... and %d more", len(result.errors) - 10)

    return result
