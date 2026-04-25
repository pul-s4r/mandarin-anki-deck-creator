"""Document / content / chunk change detection helpers."""

from __future__ import annotations

from anki_deck_generator.preprocess.fingerprints import sha256_bytes, sha256_utf8

# Aliases for incremental sync documentation parity
content_bytes_sha256 = sha256_bytes
chunk_text_sha256 = sha256_utf8
