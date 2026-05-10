"""Webhook receiver Lambda handler.

Thin entry point: verifies the Drive push notification, enqueues a sync job,
and returns 200 within the response-time SLA that Google requires.

This handler never calls ``changes.list``, never advances ``pageToken``, and
never touches PendingEdits directly.  It delegates all real work downstream.
"""

from __future__ import annotations

import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any

from anki_deck_generator.state.store import StateStore

logger = logging.getLogger(__name__)

RESOURCE_STATES_TRIGGERING_PULL = frozenset({"change", "update", "exists"})


def handle_drive_webhook(
    event: dict[str, Any],
    *,
    state_store: StateStore,
    enqueue: Any | None = None,
) -> dict[str, Any]:
    """Process a Google Drive push notification.

    *event* is expected to carry the standard API Gateway v2 payload shape
    (``headers``, ``body``, ``requestContext``, etc.).

    *enqueue* is an optional callable ``(channel_id: str) -> None`` that places
    a message on the downstream sync queue.  When ``None`` (e.g. in tests) the
    handler logs but does not enqueue.

    Returns an API-Gateway-compatible response dict.
    """
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    channel_id = headers.get("x-goog-channel-id", "")
    channel_token = headers.get("x-goog-channel-token", "")
    resource_state = headers.get("x-goog-resource-state", "")
    message_number_raw = headers.get("x-goog-message-number", "0")

    try:
        message_number = int(message_number_raw)
    except (ValueError, TypeError):
        message_number = 0

    if not channel_id:
        logger.warning("webhook: missing X-Goog-Channel-ID")
        return _response(400, "missing channel id")

    channel = state_store.get_drive_channel(channel_id)
    if channel is None:
        logger.warning("webhook: unknown channel_id=%s", channel_id)
        return _response(404, "unknown channel")

    stored_token = getattr(channel, "token", "") or ""
    if stored_token and not hmac.compare_digest(stored_token, channel_token):
        logger.warning("webhook: token mismatch for channel_id=%s", channel_id)
        return _response(401, "unauthorized")

    log_fields: dict[str, Any] = {
        "event": "drive.webhook.received",
        "channel_id": channel_id,
        "resource_state": resource_state,
        "message_number": message_number,
    }

    if resource_state == "sync":
        log_fields["enqueued"] = False
        logger.info(json.dumps(log_fields))
        return _response(200, "sync ack")

    if resource_state not in RESOURCE_STATES_TRIGGERING_PULL:
        log_fields["enqueued"] = False
        logger.info(json.dumps(log_fields))
        return _response(200, "ignored")

    if enqueue is not None:
        enqueue(channel_id)
        log_fields["enqueued"] = True
    else:
        log_fields["enqueued"] = False

    logger.info(json.dumps(log_fields))
    return _response(200, "ok")


def _response(status: int, body: str) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"message": body}),
    }
