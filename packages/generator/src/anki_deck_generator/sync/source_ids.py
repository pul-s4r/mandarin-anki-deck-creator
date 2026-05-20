"""Stable source identifiers for StateStore rows."""

from __future__ import annotations

import hashlib


def make_source_id(*, user_id: str, provider: str, external_id: str) -> str:
    payload = f"{user_id}\n{provider}\n{external_id}".encode("utf-8")
    return f"src_{hashlib.sha256(payload).hexdigest()}"
