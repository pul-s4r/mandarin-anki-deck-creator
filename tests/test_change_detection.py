from __future__ import annotations

from anki_deck_generator.preprocess.fingerprints import sha256_bytes, sha256_utf8
from anki_deck_generator.state.records import ChunkRecord, SourceRecord
from anki_deck_generator.sync.change_detection import (
    chunk_needs_llm,
    chunk_text_sha256,
    content_bytes_sha256,
    should_skip_document_by_stored_hash,
)


def test_chunk_hash_stable_across_runs() -> None:
    t = "你好世界\n第二行"
    assert chunk_text_sha256(t) == sha256_utf8(t)
    assert content_bytes_sha256(b"x") == sha256_bytes(b"x")


def test_should_skip_document_by_stored_hash() -> None:
    prev = SourceRecord(source_id="s", provider="p", external_id="e", content_sha256="abc")
    assert should_skip_document_by_stored_hash(prev, "abc") is True
    assert should_skip_document_by_stored_hash(prev, "def") is False
    assert should_skip_document_by_stored_hash(None, "abc") is False


def test_chunk_needs_llm() -> None:
    assert chunk_needs_llm(None, "x") is True
    prev = ChunkRecord(source_id="s", chunk_index=0, chunk_sha256="old")
    assert chunk_needs_llm(prev, "new") is True
    assert chunk_needs_llm(prev, "old") is False
