"""Document / content / chunk change detection helpers."""

from __future__ import annotations

from anki_deck_generator.preprocess.fingerprints import sha256_bytes, sha256_utf8
from anki_deck_generator.state.records import ChunkRecord, SourceRecord

# Stable hash helpers (public API)
content_bytes_sha256 = sha256_bytes
chunk_text_sha256 = sha256_utf8


def should_skip_document_by_stored_hash(previous: SourceRecord | None, stored_content_sha256: str) -> bool:
    """
    Return True if a prior source row exists and ``stored_content_sha256`` matches persisted ``content_sha256``.

    For local filesystem sources, ``content_sha256`` is the raw file bytes hash (see ``ResolvedLocalFileSource``).
    """
    return previous is not None and previous.content_sha256 == stored_content_sha256


def chunk_needs_llm(previous: ChunkRecord | None, chunk_sha256: str) -> bool:
    """Return True if there is no prior chunk row or the chunk text hash changed."""
    if previous is None:
        return True
    return previous.chunk_sha256 != chunk_sha256
