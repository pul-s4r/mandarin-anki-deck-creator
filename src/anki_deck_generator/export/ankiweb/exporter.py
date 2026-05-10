"""Card → Anki note mapping and export result types for AnkiConnect."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

from anki_deck_generator.state.records import CardRecord

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
