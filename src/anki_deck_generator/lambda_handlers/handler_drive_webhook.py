"""Lambda handler: Drive webhook (thin wrapper).

Delegates to the shared FastAPI ASGI handler via Mangum, or handles
the raw API Gateway event directly for low-latency responses.
"""

from __future__ import annotations

import json
import logging
import os

from anki_deck_generator.lambda_handlers.lambda_init import init_all

logger = logging.getLogger(__name__)

_initialized = False


def handler(event: dict, context: object) -> dict:
    """AWS Lambda entry point for Drive push notifications (API Gateway proxy)."""
    global _initialized
    if not _initialized:
        init_all()
        _initialized = True

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    channel_id = headers.get("x-goog-channel-id", "")
    channel_token = headers.get("x-goog-channel-token", "")
    resource_state = headers.get("x-goog-resource-state", "")

    logger.info(
        "Lambda Drive webhook: channel=%r state=%r", channel_id, resource_state
    )

    if resource_state == "sync":
        return {"statusCode": 200, "body": ""}

    if not channel_id:
        return {"statusCode": 400, "body": "Missing channel ID"}

    # Delegate to shared drive_events service.
    try:
        _process(channel_id=channel_id, resource_state=resource_state)
    except Exception as exc:
        logger.error("Lambda Drive webhook error: %s", exc)
        return {"statusCode": 500, "body": str(exc)}

    return {"statusCode": 200, "body": ""}


def _process(*, channel_id: str, resource_state: str) -> None:
    from anki_deck_generator.sync.drive_events import enqueue_mode_a

    if resource_state in {"change", "update", "exists"}:
        enqueue_mode_a(channel_id)
