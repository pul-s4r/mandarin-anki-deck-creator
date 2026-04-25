from __future__ import annotations

from anki_deck_generator.preprocess.fingerprints import sha256_bytes, sha256_utf8
from anki_deck_generator.sync.change_detection import chunk_text_sha256, content_bytes_sha256


def test_chunk_hash_stable_across_runs() -> None:
    t = "你好世界\n第二行"
    assert chunk_text_sha256(t) == sha256_utf8(t)
    assert content_bytes_sha256(b"x") == sha256_bytes(b"x")
