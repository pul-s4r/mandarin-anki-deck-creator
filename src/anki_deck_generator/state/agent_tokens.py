"""Agent bearer-token hashing (bcrypt; optional server extra)."""

from __future__ import annotations

import secrets


def generate_agent_token() -> str:
    """Return a URL-safe opaque bearer token."""
    return secrets.token_urlsafe(32)


def hash_agent_token(token: str) -> str:
    import bcrypt

    digest = bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt(rounds=12))
    return digest.decode("utf-8")


def verify_agent_token(token: str, token_hash: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw(token.encode("utf-8"), token_hash.encode("utf-8"))
    except ValueError:
        return False
