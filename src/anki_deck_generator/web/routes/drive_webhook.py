"""Drive push-notification webhook receiver (D6).

Handles POST /api/drive/notifications from Google.
Returns 200 quickly — no LLM, no blocking sync work in the request path.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from anki_deck_generator.sync.drive_watch import verify_channel_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/drive", tags=["drive"])

_CHANGE_STATES = {"change", "update", "exists"}


@router.post(
    "/notifications",
    status_code=status.HTTP_200_OK,
    summary="Receive Drive push notification",
    description=(
        "Endpoint registered as the webhook address for Google Drive changes.watch. "
        "Verifies channel token, dispatches Mode A for change/update/exists states, "
        "and returns 200 immediately."
    ),
)
async def drive_notifications(
    request: Request,
    x_goog_channel_id: str | None = Header(default=None, alias="X-Goog-Channel-ID"),
    x_goog_channel_token: str | None = Header(default=None, alias="X-Goog-Channel-Token"),
    x_goog_resource_id: str | None = Header(default=None, alias="X-Goog-Resource-ID"),
    x_goog_resource_state: str | None = Header(default=None, alias="X-Goog-Resource-State"),
    x_goog_message_number: str | None = Header(default=None, alias="X-Goog-Message-Number"),
) -> Response:
    """Process a Drive push notification."""
    if not x_goog_channel_id:
        logger.warning("Drive webhook: missing X-Goog-Channel-ID")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing channel ID")

    if not x_goog_resource_state:
        logger.warning("Drive webhook: missing X-Goog-Resource-State for channel %r", x_goog_channel_id)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing resource state")

    resource_state = x_goog_resource_state.lower()

    # sync: initial acknowledgement notification — just return 200.
    if resource_state == "sync":
        logger.info(
            "Drive webhook: sync notification for channel %r (message %s)",
            x_goog_channel_id,
            x_goog_message_number,
        )
        return Response(status_code=status.HTTP_200_OK)

    # Verify channel token for non-sync notifications.
    state_store = getattr(request.app.state, "state_store", None)
    if state_store is not None:
        channel_rec = state_store.get_drive_channel(x_goog_channel_id)
        if channel_rec is None:
            logger.warning("Drive webhook: unknown channel %r; ignoring", x_goog_channel_id)
            return Response(status_code=status.HTTP_200_OK)

        if channel_rec.channel_token and x_goog_channel_token:
            if not verify_channel_token(channel_rec.channel_token, x_goog_channel_token):
                logger.warning(
                    "Drive webhook: token mismatch for channel %r", x_goog_channel_id
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Token mismatch"
                )

    if resource_state in _CHANGE_STATES:
        logger.info(
            "Drive webhook: change notification for channel %r (state=%r, message=%s)",
            x_goog_channel_id,
            resource_state,
            x_goog_message_number,
        )
        from anki_deck_generator.sync.drive_events import enqueue_mode_a

        enqueue_mode_a(x_goog_channel_id)

    elif resource_state == "remove":
        logger.info(
            "Drive webhook: channel %r removed/expired — renewal signal",
            x_goog_channel_id,
        )
        # Signal for renewal; renewal is handled out-of-band by the CLI or EventBridge.

    else:
        logger.info(
            "Drive webhook: unhandled resource state %r for channel %r",
            resource_state,
            x_goog_channel_id,
        )

    return Response(status_code=status.HTTP_200_OK)
