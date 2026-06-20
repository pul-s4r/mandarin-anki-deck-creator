"""Drive watch channel registration lifecycle (D4).

Handles registering, renewing, and stopping Google Drive push-notification channels.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anki_deck_generator.state.records import DriveChannelRecord
    from anki_deck_generator.state.store import StateStore

logger = logging.getLogger(__name__)

_DEFAULT_EXPIRATION_HOURS = 24 * 7  # 7 days (Drive max is 7 days for some resources)
_RENEW_BEFORE_HOURS = 48


def _load_drive_provider(credentials_file: str) -> Any:
    import importlib

    importlib.import_module("anki_deck_generator.integrations.google_drive")
    from anki_deck_generator.integrations.registry import get_provider

    provider = get_provider("google-drive")
    provider.authenticate({"credentials_file": credentials_file})
    return provider


def register_watch_channel(
    *,
    credentials_file: str,
    source_set_name: str,
    webhook_url: str,
    state_store: StateStore,
    user_id: str = "default",
    channel_id: str | None = None,
    expiration_hours: int = _DEFAULT_EXPIRATION_HOURS,
) -> DriveChannelRecord:
    """Register a new Drive watch channel for *source_set_name*.

    Obtains a start page token, calls ``changes.watch``, and persists the
    resulting ``DriveChannelRecord`` to *state_store*.
    """
    from anki_deck_generator.state.records import DriveChannelRecord

    provider = _load_drive_provider(credentials_file)

    cid = channel_id or f"anki-pipeline-{source_set_name}-{secrets.token_hex(8)}"
    channel_token = secrets.token_hex(32)
    page_token = provider.get_start_page_token()

    now = datetime.now(UTC)
    expiration_ms = int((now + timedelta(hours=expiration_hours)).timestamp() * 1000)

    resp = provider.watch_changes(
        page_token=page_token,
        address=webhook_url,
        channel_id=cid,
        channel_token=channel_token,
        expiration_ms=expiration_ms,
    )

    resource_id = str(resp.get("resourceId", ""))
    expiration_epoch_ms_str = resp.get("expiration", "")
    expiration_dt: datetime | None = None
    if expiration_epoch_ms_str:
        try:
            expiration_dt = datetime.fromtimestamp(int(expiration_epoch_ms_str) / 1000, tz=UTC)
        except (ValueError, OSError):
            logger.warning("Could not parse Drive channel expiration: %r", expiration_epoch_ms_str)

    rec = DriveChannelRecord(
        channel_id=cid,
        resource_id=resource_id,
        page_token=page_token,
        expiration=expiration_dt,
        user_id=user_id,
        source_set_name=source_set_name,
        channel_token=channel_token,
    )
    state_store.upsert_drive_channel(rec)
    logger.info(
        "Registered Drive watch channel %r for source set %r (expires %s)",
        cid,
        source_set_name,
        expiration_dt,
    )
    return rec


def unregister_watch_channel(
    *,
    channel_id: str,
    credentials_file: str,
    state_store: StateStore,
    user_id: str = "default",
) -> None:
    """Stop a Drive watch channel and remove it from state."""
    from anki_deck_generator.state.records import DriveChannelRecord

    rec = state_store.get_drive_channel(channel_id)
    if rec is None:
        raise KeyError(f"Channel {channel_id!r} not found in state")

    provider = _load_drive_provider(credentials_file)
    provider.stop_channel(channel_id, rec.resource_id)

    # Mark as deactivated by clearing expiration and resource_id.
    deactivated = DriveChannelRecord(
        channel_id=rec.channel_id,
        resource_id="",
        page_token=rec.page_token,
        expiration=None,
        user_id=rec.user_id,
        source_set_name=rec.source_set_name,
        channel_token=rec.channel_token,
    )
    state_store.upsert_drive_channel(deactivated)
    logger.info("Stopped Drive watch channel %r", channel_id)


def renew_expiring_channels(
    *,
    credentials_file: str,
    webhook_url: str,
    state_store: StateStore,
    user_id: str = "default",
    renew_before_hours: int = _RENEW_BEFORE_HOURS,
) -> list[str]:
    """Renew watch channels expiring within *renew_before_hours*.

    Returns a list of channel IDs that were renewed.
    """
    now = datetime.now(UTC)
    threshold = now + timedelta(hours=renew_before_hours)

    sqlite_store = state_store
    if not hasattr(sqlite_store, "list_drive_channels"):
        logger.warning("State store does not support list_drive_channels; skipping renewal")
        return []

    all_channels: list = sqlite_store.list_drive_channels(user_id=user_id)  # type: ignore[attr-defined]
    renewed: list[str] = []

    for rec in all_channels:
        if rec.expiration is None:
            continue
        if rec.expiration > threshold:
            continue
        if not rec.source_set_name:
            logger.warning("Channel %r has no source_set_name; cannot renew", rec.channel_id)
            continue

        logger.info("Renewing Drive watch channel %r (expires %s)", rec.channel_id, rec.expiration)
        try:
            new_rec = register_watch_channel(
                credentials_file=credentials_file,
                source_set_name=rec.source_set_name,
                webhook_url=webhook_url,
                state_store=state_store,
                user_id=user_id,
            )
            # Stop the old channel after successfully registering the new one.
            provider = _load_drive_provider(credentials_file)
            try:
                provider.stop_channel(rec.channel_id, rec.resource_id)
            except Exception as exc:
                logger.warning("Could not stop old channel %r: %s", rec.channel_id, exc)
            renewed.append(new_rec.channel_id)
        except Exception as exc:
            logger.error("Failed to renew channel %r: %s", rec.channel_id, exc)

    return renewed


def verify_channel_token(stored_token: str, provided_token: str) -> bool:
    """Constant-time comparison of channel tokens."""
    return hmac.compare_digest(stored_token.encode(), provided_token.encode())
