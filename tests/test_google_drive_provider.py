"""Google Drive provider tests (mocked HTTP client; no network)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from anki_deck_generator.errors import AuthenticationError, IntegrationError
from anki_deck_generator.integrations import google_drive as gd


@pytest.fixture
def drive_provider() -> gd.GoogleDriveProvider:
    p = gd.GoogleDriveProvider()
    p._creds = MagicMock()
    p._service = MagicMock()
    return p


def test_get_revision_returns_tuple(drive_provider: gd.GoogleDriveProvider) -> None:
    files_api = drive_provider._service.files.return_value
    files_api.get.return_value.execute.return_value = {"headRevisionId": "revA", "etag": "et1"}
    assert drive_provider.get_revision("fileX") == ("revA", "et1")
    files_api.get.assert_called_once()
    _, kwargs = files_api.get.call_args
    assert kwargs["fileId"] == "fileX"


def test_list_sources_pagination(drive_provider: gd.GoogleDriveProvider) -> None:
    files_api = drive_provider._service.files.return_value
    files_api.list.return_value.execute.side_effect = [
        {
            "files": [
                {
                    "id": "a",
                    "name": "one.pdf",
                    "mimeType": "application/pdf",
                    "modifiedTime": "t",
                    "headRevisionId": "h1",
                    "etag": "e1",
                    "trashed": False,
                }
            ],
            "nextPageToken": "tok",
        },
        {
            "files": [
                {
                    "id": "b",
                    "name": "two.md",
                    "mimeType": "text/markdown",
                    "modifiedTime": "t2",
                    "headRevisionId": "",
                    "etag": "e2",
                    "trashed": False,
                }
            ],
        },
    ]
    rows = drive_provider.list_sources(folder_id="folder1")
    assert [r["id"] for r in rows] == ["a", "b"]
    assert files_api.list.call_count == 2


def test_import_google_doc_exports_docx(drive_provider: gd.GoogleDriveProvider) -> None:
    files_api = drive_provider._service.files.return_value
    meta = {
        "id": "doc1",
        "name": "Lesson.gdoc",
        "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "t",
        "headRevisionId": "hr",
        "etag": "eg",
        "trashed": False,
    }
    files_api.get.return_value.execute.return_value = meta
    fake_req = object()
    files_api.export_media.return_value = fake_req

    with patch.object(gd, "_download_media_request", return_value=b"%DOCX%") as dl:
        result = drive_provider.import_documents(file_ids=["doc1"])

    dl.assert_called_once_with(fake_req)
    files_api.export_media.assert_called_once_with(
        fileId="doc1",
        mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert len(result.documents) == 1
    assert result.documents[0].format == "docx"
    assert result.documents[0].data == b"%DOCX%"
    assert result.documents[0].external_id == "doc1"


def test_import_native_pdf_uses_get_media(drive_provider: gd.GoogleDriveProvider) -> None:
    files_api = drive_provider._service.files.return_value
    meta = {
        "id": "pdf1",
        "name": "x.pdf",
        "mimeType": "application/pdf",
        "modifiedTime": "t",
        "headRevisionId": "",
        "etag": "",
        "trashed": False,
    }
    files_api.get.return_value.execute.return_value = meta
    fake_req = object()
    files_api.get_media.return_value = fake_req

    with patch.object(gd, "_download_media_request", return_value=b"%PDF%"):
        result = drive_provider.import_documents(file_ids=["pdf1"])

    files_api.get_media.assert_called_once()
    assert result.documents[0].format == "pdf"
    assert result.documents[0].data == b"%PDF%"


def test_import_skips_trashed_file(drive_provider: gd.GoogleDriveProvider, caplog: pytest.LogCaptureFixture) -> None:
    files_api = drive_provider._service.files.return_value
    meta = {
        "id": "gone",
        "name": "bad.pdf",
        "mimeType": "application/pdf",
        "modifiedTime": "t",
        "headRevisionId": "",
        "etag": "",
        "trashed": True,
    }
    files_api.get.return_value.execute.return_value = meta

    import logging

    with caplog.at_level(logging.WARNING):
        result = drive_provider.import_documents(file_ids=["gone"])

    assert result.documents == []
    assert "Skipping trashed" in caplog.text


def test_http_403_raises_authentication_error(drive_provider: gd.GoogleDriveProvider) -> None:
    resp = MagicMock(status=403)
    files_api = drive_provider._service.files.return_value
    files_api.get.return_value.execute.side_effect = HttpError(resp, b"{}")
    with pytest.raises(AuthenticationError):
        drive_provider.get_revision("x")


def test_http_404_raises_integration_error(drive_provider: gd.GoogleDriveProvider) -> None:
    resp = MagicMock(status=404)
    files_api = drive_provider._service.files.return_value
    files_api.get.return_value.execute.side_effect = HttpError(resp, b"{}")
    with pytest.raises(IntegrationError):
        drive_provider.get_revision("missing")


def test_oauth_writes_token_json(tmp_path: Path) -> None:
    secrets = tmp_path / "cs.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "x", "client_secret": "y"}}), encoding="utf-8")
    token_out = tmp_path / "tok.json"

    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "abc"}'

    mock_flow = MagicMock()
    mock_flow.run_local_server.return_value = mock_creds

    with patch("google_auth_oauthlib.flow.InstalledAppFlow") as flow_cls:
        flow_cls.from_client_secrets_file.return_value = mock_flow
        gd.run_google_drive_oauth_and_save_token(client_secrets=secrets, token_file=token_out)

    flow_cls.from_client_secrets_file.assert_called_once()
    assert json.loads(token_out.read_text(encoding="utf-8")) == {"token": "abc"}
