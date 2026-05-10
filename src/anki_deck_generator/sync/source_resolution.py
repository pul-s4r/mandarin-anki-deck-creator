"""Resolve configured sources against StateStore (filesystem ingest, hashes, skip decisions)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from anki_deck_generator.config.source_sets import LocalFileSource
from anki_deck_generator.ingest.router import extract_text_from_bytes
from anki_deck_generator.preprocess.fingerprints import sha256_bytes
from anki_deck_generator.preprocess.normalize import normalize_unicode, optional_drop_metadata_lines
from anki_deck_generator.state.records import SourceRecord
from anki_deck_generator.state.store import StateStore
from anki_deck_generator.sync.change_detection import should_skip_document_by_stored_hash
from anki_deck_generator.sync.source_ids import make_source_id

if TYPE_CHECKING:
    from anki_deck_generator.config.settings import Settings


def suffix_to_ingest_format(suffix: str) -> str | None:
    """Map file suffix to ``extract_text_from_bytes(..., format=...)`` name."""
    s = suffix.lower()
    if s == ".pdf":
        return "pdf"
    if s in {".md", ".markdown"}:
        return "markdown"
    if s == ".docx":
        return "docx"
    return None


@dataclass(frozen=True)
class ResolvedLocalFileSource:
    """A local file source after reading bytes, optional ingest, and document-level skip check."""

    source: LocalFileSource
    source_id: str
    raw_bytes_sha256: str
    """SHA-256 of raw file bytes; persisted on ``SourceRecord.content_sha256`` for local filesystem sources."""

    ingest_format: str
    """Ingest format passed to ``extract_text_from_bytes``; empty when ``skipped_document`` is True."""

    normalized_text: str
    """Unicode-normalized, optionally metadata-stripped note text; empty when ``skipped_document`` is True."""

    revision_id: str
    previous: SourceRecord | None
    skipped_document: bool


def resolve_local_file_source(
    src: LocalFileSource,
    *,
    settings: Settings,
    state_store: StateStore,
    user_id: str,
) -> ResolvedLocalFileSource:
    """
    Read the file, compare raw-byte hash to the stored ``SourceRecord.content_sha256``, optionally ingest to text.

    For ``local-filesystem`` sources, ``content_sha256`` stores the raw file bytes hash (not extracted text).
    """
    sid = make_source_id(user_id=user_id, provider=src.provider, external_id=src.external_id)
    raw = src.path.read_bytes()
    raw_hash = sha256_bytes(raw)
    prev = state_store.get_source_record(src.provider, src.external_id, user_id=user_id)
    revision_id = str(src.path.stat().st_mtime_ns)

    if should_skip_document_by_stored_hash(prev, raw_hash):
        return ResolvedLocalFileSource(
            source=src,
            source_id=sid,
            raw_bytes_sha256=raw_hash,
            ingest_format="",
            normalized_text="",
            revision_id=revision_id,
            previous=prev,
            skipped_document=True,
        )

    fmt = suffix_to_ingest_format(src.path.suffix)
    if fmt is None:
        raise ValueError(f"Unsupported file type for incremental sync: {src.path}")

    text = extract_text_from_bytes(raw, format=fmt)
    text = normalize_unicode(text)
    text = optional_drop_metadata_lines(text, enabled=settings.skip_lines_filter)

    return ResolvedLocalFileSource(
        source=src,
        source_id=sid,
        raw_bytes_sha256=raw_hash,
        ingest_format=fmt,
        normalized_text=text,
        revision_id=revision_id,
        previous=prev,
        skipped_document=False,
    )
