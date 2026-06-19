"""Tests for Drive changes.list pagination and file ID extraction (D4)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from anki_deck_generator.integrations import google_drive as gd


@pytest.fixture
def drive_provider() -> gd.GoogleDriveProvider:
    p = gd.GoogleDriveProvider()
    p._creds = MagicMock()
    p._service = MagicMock()
    return p


# ─────────────────────────── get_start_page_token ──────────────────────── #


def test_get_start_page_token_returns_string(drive_provider: gd.GoogleDriveProvider) -> None:
    changes_api = drive_provider._service.changes.return_value
    changes_api.getStartPageToken.return_value.execute.return_value = {
        "startPageToken": "tok123"
    }
    assert drive_provider.get_start_page_token() == "tok123"
    changes_api.getStartPageToken.assert_called_once()


def test_get_start_page_token_passes_supports_all_drives(drive_provider: gd.GoogleDriveProvider) -> None:
    changes_api = drive_provider._service.changes.return_value
    changes_api.getStartPageToken.return_value.execute.return_value = {"startPageToken": "t"}
    drive_provider.get_start_page_token()
    _, kwargs = changes_api.getStartPageToken.call_args
    assert kwargs.get("supportsAllDrives") is True
    assert kwargs.get("includeItemsFromAllDrives") is True


# ─────────────────────────── list_changes ──────────────────────────────── #


def test_list_changes_single_page(drive_provider: gd.GoogleDriveProvider) -> None:
    changes_api = drive_provider._service.changes.return_value
    changes_api.list.return_value.execute.return_value = {
        "newStartPageToken": "next_tok",
        "changes": [
            {"fileId": "file-a", "removed": False, "file": {"trashed": False}},
            {"fileId": "file-b", "removed": False, "file": {"trashed": False}},
        ],
    }
    result = drive_provider.list_changes("start_tok")
    assert result["file_ids"] == ["file-a", "file-b"]
    assert result["new_start_page_token"] == "next_tok"
    assert result["next_page_token"] == ""
    changes_api.list.assert_called_once()


def test_list_changes_pagination(drive_provider: gd.GoogleDriveProvider) -> None:
    changes_api = drive_provider._service.changes.return_value
    changes_api.list.return_value.execute.side_effect = [
        {
            "nextPageToken": "page2",
            "changes": [{"fileId": "file-1", "removed": False, "file": {"trashed": False}}],
        },
        {
            "newStartPageToken": "final_tok",
            "changes": [{"fileId": "file-2", "removed": False, "file": {"trashed": False}}],
        },
    ]
    result = drive_provider.list_changes("initial")
    assert result["file_ids"] == ["file-1", "file-2"]
    assert result["new_start_page_token"] == "final_tok"
    assert changes_api.list.call_count == 2


def test_list_changes_excludes_removed(drive_provider: gd.GoogleDriveProvider) -> None:
    changes_api = drive_provider._service.changes.return_value
    changes_api.list.return_value.execute.return_value = {
        "newStartPageToken": "t",
        "changes": [
            {"fileId": "kept", "removed": False, "file": {"trashed": False}},
            {"fileId": "gone", "removed": True, "file": None},
        ],
    }
    result = drive_provider.list_changes("tok")
    assert result["file_ids"] == ["kept"]


def test_list_changes_excludes_trashed(drive_provider: gd.GoogleDriveProvider) -> None:
    changes_api = drive_provider._service.changes.return_value
    changes_api.list.return_value.execute.return_value = {
        "newStartPageToken": "t",
        "changes": [
            {"fileId": "kept", "removed": False, "file": {"trashed": False}},
            {"fileId": "trash", "removed": False, "file": {"trashed": True}},
        ],
    }
    result = drive_provider.list_changes("tok")
    assert result["file_ids"] == ["kept"]


def test_list_changes_empty_result(drive_provider: gd.GoogleDriveProvider) -> None:
    changes_api = drive_provider._service.changes.return_value
    changes_api.list.return_value.execute.return_value = {
        "newStartPageToken": "same",
        "changes": [],
    }
    result = drive_provider.list_changes("same")
    assert result["file_ids"] == []
    assert result["new_start_page_token"] == "same"


def test_list_changes_http_error_raises_integration_error(drive_provider: gd.GoogleDriveProvider) -> None:
    from anki_deck_generator.errors import IntegrationError

    changes_api = drive_provider._service.changes.return_value
    resp = MagicMock(status=500)
    changes_api.list.return_value.execute.side_effect = HttpError(resp, b"server error")
    with pytest.raises(IntegrationError):
        drive_provider.list_changes("tok")


# ───────────────────────── watch_changes ──────────────────────────────── #


def test_watch_changes_returns_response_dict(drive_provider: gd.GoogleDriveProvider) -> None:
    changes_api = drive_provider._service.changes.return_value
    changes_api.watch.return_value.execute.return_value = {
        "id": "chan-1",
        "resourceId": "res-1",
        "expiration": "9999999999000",
    }
    result = drive_provider.watch_changes(
        page_token="tok",
        address="https://example.com/webhook",
        channel_id="chan-1",
        channel_token="secret-token",
    )
    assert result["resourceId"] == "res-1"
    _, kwargs = changes_api.watch.call_args
    body = kwargs["body"]
    assert body["id"] == "chan-1"
    assert body["token"] == "secret-token"
    assert body["address"] == "https://example.com/webhook"
    assert body["type"] == "web_hook"


def test_watch_changes_includes_expiration_when_set(drive_provider: gd.GoogleDriveProvider) -> None:
    changes_api = drive_provider._service.changes.return_value
    changes_api.watch.return_value.execute.return_value = {"id": "c", "resourceId": "r"}
    drive_provider.watch_changes(
        page_token="tok",
        address="https://example.com/wh",
        channel_id="c",
        channel_token="t",
        expiration_ms=9999000000000,
    )
    _, kwargs = changes_api.watch.call_args
    assert kwargs["body"]["expiration"] == "9999000000000"


# ─────────────────────────── stop_channel ──────────────────────────────── #


def test_stop_channel_calls_channels_stop(drive_provider: gd.GoogleDriveProvider) -> None:
    channels_api = drive_provider._service.channels.return_value
    channels_api.stop.return_value.execute.return_value = {}
    drive_provider.stop_channel("chan-1", "res-1")
    channels_api.stop.assert_called_once()
    _, kwargs = channels_api.stop.call_args
    assert kwargs["body"]["id"] == "chan-1"
    assert kwargs["body"]["resourceId"] == "res-1"


def test_stop_channel_http_403_raises_authentication_error(drive_provider: gd.GoogleDriveProvider) -> None:
    from anki_deck_generator.errors import AuthenticationError

    channels_api = drive_provider._service.channels.return_value
    resp = MagicMock(status=403)
    channels_api.stop.return_value.execute.side_effect = HttpError(resp, b"forbidden")
    with pytest.raises(AuthenticationError):
        drive_provider.stop_channel("chan-1", "res-1")
