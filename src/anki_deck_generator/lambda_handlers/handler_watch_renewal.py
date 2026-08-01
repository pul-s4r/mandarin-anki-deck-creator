"""Lambda handler: Drive watch channel renewal (thin wrapper).

Invoked by EventBridge on a schedule (e.g., every 24 h) to renew channels
expiring within the next 48 h.
"""

from __future__ import annotations

import logging
import os

from anki_deck_generator.lambda_handlers.lambda_init import init_all

logger = logging.getLogger(__name__)

_initialized = False


def handler(event: dict, context: object) -> dict:
    """AWS Lambda entry point for watch channel renewal."""
    global _initialized
    if not _initialized:
        init_all()
        _initialized = True

    credentials_file = os.environ.get("GOOGLE_DRIVE_CREDENTIALS_FILE", "")
    webhook_url = os.environ.get("DRIVE_WEBHOOK_URL", "")
    user_id = event.get("user_id", "default")

    if not credentials_file:
        return {"statusCode": 400, "body": "Missing GOOGLE_DRIVE_CREDENTIALS_FILE"}
    if not webhook_url:
        return {"statusCode": 400, "body": "Missing DRIVE_WEBHOOK_URL"}

    try:
        renewed = _renew(
            credentials_file=credentials_file,
            webhook_url=webhook_url,
            user_id=user_id,
        )
        logger.info("Renewed %d channel(s)", len(renewed))
        return {"statusCode": 200, "body": f"Renewed: {renewed}"}
    except Exception as exc:
        logger.error("Lambda watch renewal error: %s", exc)
        return {"statusCode": 500, "body": str(exc)}


def _renew(*, credentials_file: str, webhook_url: str, user_id: str) -> list[str]:
    from anki_deck_generator.config.settings import Settings
    from anki_deck_generator.state import get_store
    from anki_deck_generator.sync.drive_watch import renew_expiring_channels

    settings = Settings()
    store = get_store(settings)
    if store is None:
        raise RuntimeError("State store not configured")

    return renew_expiring_channels(
        credentials_file=credentials_file,
        webhook_url=webhook_url,
        state_store=store,
        user_id=user_id,
    )
