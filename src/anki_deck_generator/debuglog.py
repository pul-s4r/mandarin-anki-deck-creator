from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_LOG_PATH = Path("/home/jjmoey/Projects/anki_deck_generator/.cursor/debug-815477.log")
_SESSION_ID = "815477"


def debug_log(*, run_id: str, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    payload = {
        "sessionId": _SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": _redact(data),
        "timestamp": int(time.time() * 1000),
    }
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # Debug logging must never break the pipeline.
        return


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    # Avoid accidentally logging credentials/secrets.
    redacted: dict[str, Any] = {}
    for k, v in data.items():
        kl = k.lower()
        if any(tok in kl for tok in ("token", "secret", "password", "key", "bearer")):
            redacted[k] = "[redacted]"
        else:
            redacted[k] = v
    return redacted

