"""Google Drive integration (optional ``google-drive`` extra)."""

from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
from typing import Any

from anki_deck_generator.errors import AuthenticationError, IntegrationError
from anki_deck_generator.integrations.base import ImportedDocument, ImportResult, IntegrationProvider
from anki_deck_generator.integrations.registry import register_provider

logger = logging.getLogger(__name__)

READONLY_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


def default_google_drive_token_path() -> Path:
    """Default OAuth token path (``XDG_CONFIG_HOME`` / ``~/.config``)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return (base / "anki-notes-pipeline" / "google-drive-token.json").expanduser().resolve()

_SUPPORTED_LIST_MIMES: tuple[str, ...] = (
    "application/pdf",
    "application/vnd.google-apps.document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
)


def _optional_google_imports() -> tuple[Any, Any, Any]:
    try:
        from google.oauth2 import service_account
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - caught when extras missing
        raise IntegrationError(
            "Google Drive integration requires optional dependencies. "
            "Install with: pip install 'anki-deck-generator[google-drive]'"
        ) from exc
    return service_account, Credentials, build


def _map_http_error(exc: BaseException, *, context: str) -> None:
    from googleapiclient.errors import HttpError

    if not isinstance(exc, HttpError):
        raise IntegrationError(f"{context}: {exc}") from exc
    status = getattr(exc.resp, "status", None)
    detail = getattr(exc, "content", b"")[:512]
    msg = f"{context} (HTTP {status}): {detail!r}"
    if status == 403:
        raise AuthenticationError(msg) from exc
    if status == 404:
        raise IntegrationError(msg) from exc
    raise IntegrationError(msg) from exc


def load_credentials_from_file(path: Path, *, scopes: tuple[str, ...] | None = None) -> Any:
    """Load OAuth user credentials JSON or a service-account key file."""
    service_account, Credentials, _build = _optional_google_imports()
    sc = list(scopes or (READONLY_DRIVE_SCOPE,))
    raw_text = path.read_text(encoding="utf-8").strip()
    if not raw_text:
        raise IntegrationError(
            f"Google credentials file is empty: {path}. "
            "For OAuth, run: anki-notes-pipeline auth google-drive --client-secrets PATH/to/client_secret*.json"
        )
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise IntegrationError(f"Google credentials file is not valid JSON: {path}") from exc

    if data.get("type") == "service_account":
        return service_account.Credentials.from_service_account_file(str(path), scopes=sc)

    if "installed" in data or "web" in data:
        raise IntegrationError(
            f"{path} looks like OAuth *client* secrets (desktop/API console JSON), not a saved user token. "
            "Run once: anki-notes-pipeline auth google-drive --client-secrets <that client_secrets.json file>. "
            "Then set credentials_file in YAML (or --credentials-file) to the *token* path written by that command "
            "(default ~/.config/anki-notes-pipeline/google-drive-token.json), not the client secrets file."
        )

    if not isinstance(data, dict):
        raise IntegrationError(f"Google OAuth token JSON must be a JSON object: {path}")

    required = ("refresh_token", "client_id", "client_secret")
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise IntegrationError(
            f"OAuth token file {path} is missing required field(s): {', '.join(missing)}. "
            "Complete the browser login with: anki-notes-pipeline auth google-drive --client-secrets <client_secrets.json>. "
            "Use the generated token file as credentials_file — not client_secrets.json and not an empty placeholder."
        )

    try:
        return Credentials.from_authorized_user_file(str(path), scopes=sc)
    except ValueError as exc:
        raise IntegrationError(
            f"Could not load OAuth credentials from {path}: {exc}. "
            "Re-run auth google-drive or confirm the file is the JSON saved after the browser flow."
        ) from exc


def run_google_drive_oauth_and_save_token(*, client_secrets: Path, token_file: Path) -> None:
    """Interactive OAuth install flow; persists refreshed credentials to ``token_file``."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise IntegrationError(
            "Google Drive OAuth requires optional dependencies. "
            "Install with: pip install 'anki-deck-generator[google-drive]'"
        ) from exc

    token_file = token_file.expanduser().resolve()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=[READONLY_DRIVE_SCOPE])
    creds = flow.run_local_server(port=0)
    token_file.write_text(creds.to_json(), encoding="utf-8")


def _download_media_request(request: Any) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()


def _mime_to_import_format(mime: str, *, exported_docx: bool) -> str:
    if exported_docx:
        return "docx"
    m = mime.lower()
    if m == "application/pdf":
        return "pdf"
    if m == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return "docx"
    if m in {"text/markdown"}:
        return "markdown"
    return "txt"


def _list_query(folder_id: str) -> str:
    mime_clause = " or ".join(f"mimeType='{m}'" for m in _SUPPORTED_LIST_MIMES)
    return f"'{folder_id}' in parents and trashed = false and ({mime_clause})"


def _normalize_file_record(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": raw["id"],
        "name": raw.get("name", ""),
        "mimeType": raw.get("mimeType", ""),
        "modifiedTime": raw.get("modifiedTime", ""),
        "headRevisionId": raw.get("headRevisionId") or "",
        "etag": raw.get("etag") or "",
        "trashed": bool(raw.get("trashed")),
    }


@register_provider("google-drive")
class GoogleDriveProvider(IntegrationProvider):
    """Fetch supported lesson files from Google Drive API v3."""

    name = "google-drive"

    def __init__(self) -> None:
        self._creds: Any | None = None
        self._service: Any | None = None

    def authenticate(self, credentials: dict) -> None:
        _, _, build = _optional_google_imports()

        cred_obj: Any | None = credentials.get("credentials")
        if cred_obj is not None:
            self._creds = cred_obj
            self._service = build("drive", "v3", credentials=self._creds, cache_discovery=False)
            return None

        cred_path_raw = credentials.get("credentials_file") or credentials.get("service_account_file")
        token_file = credentials.get("token_file")

        if cred_path_raw:
            path = Path(str(cred_path_raw)).expanduser().resolve()
            self._creds = load_credentials_from_file(path)
        elif token_file:
            path = Path(str(token_file)).expanduser().resolve()
            self._creds = load_credentials_from_file(path)
        else:
            raise IntegrationError(
                "GoogleDriveProvider.authenticate requires credentials_file, "
                "service_account_file, token_file, or a credentials object"
            )

        self._service = build("drive", "v3", credentials=self._creds, cache_discovery=False)
        return None

    def _svc(self) -> Any:
        if self._service is None:
            raise IntegrationError("Google Drive provider is not authenticated")
        return self._service

    def list_sources(self, *, folder_id: str, **kwargs: Any) -> list[dict]:
        svc = self._svc()
        q = _list_query(folder_id)
        page_token: str | None = None
        out: list[dict] = []
        try:
            while True:
                req = svc.files().list(
                    q=q,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, headRevisionId, trashed)",
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                resp = req.execute()
                for raw in resp.get("files", []):
                    out.append(_normalize_file_record(raw))
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
        except Exception as exc:
            _map_http_error(exc, context="listing Drive folder")
        return out

    def get_revision(self, file_id: str) -> tuple[str, str]:
        svc = self._svc()
        try:
            body = svc.files().get(
                fileId=file_id,
                fields="headRevisionId",
                supportsAllDrives=True,
            ).execute()
        except Exception as exc:
            _map_http_error(exc, context=f"Drive metadata get for {file_id!r}")
        # Drive API v3 does not expose File.etag in field masks; revision id is enough for skip logic.
        return (body.get("headRevisionId") or "", "")

    def _fetch_one_meta(self, file_id: str) -> dict[str, Any]:
        svc = self._svc()
        try:
            raw = svc.files().get(
                fileId=file_id,
                fields="id, name, mimeType, modifiedTime, headRevisionId, trashed",
                supportsAllDrives=True,
            ).execute()
        except Exception as exc:
            _map_http_error(exc, context=f"Drive file get for {file_id!r}")
        return _normalize_file_record(raw)

    def fetch_file_metadata(self, file_id: str) -> dict[str, Any]:
        """Return Drive file metadata (same shape as ``list_sources`` rows)."""
        return self._fetch_one_meta(file_id)

    def _download_native_bytes(self, file_id: str) -> bytes:
        svc = self._svc()
        try:
            request = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
            return _download_media_request(request)
        except Exception as exc:
            _map_http_error(exc, context=f"Drive download for {file_id!r}")

    def _export_google_doc_docx(self, file_id: str) -> bytes:
        svc = self._svc()
        export_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        try:
            request = svc.files().export_media(fileId=file_id, mimeType=export_mime)
            return _download_media_request(request)
        except Exception as exc:
            _map_http_error(exc, context=f"Drive export Google Doc {file_id!r}")

    def import_documents(
        self,
        *,
        folder_id: str | None = None,
        file_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> ImportResult:
        entries: dict[str, dict[str, Any]] = {}

        if folder_id:
            for row in self.list_sources(folder_id=folder_id):
                entries[row["id"]] = row

        if file_ids:
            for fid in file_ids:
                if fid not in entries:
                    entries[fid] = self._fetch_one_meta(fid)

        if not entries:
            raise IntegrationError("import_documents requires folder_id and/or non-empty file_ids")

        docs: list[ImportedDocument] = []
        desc_parts: list[str] = []
        if folder_id:
            desc_parts.append(f"folder {folder_id!r}")
        if file_ids:
            desc_parts.append(f"{len(file_ids)} explicit file(s)")
        description = "Google Drive: " + ", ".join(desc_parts)

        for fid, meta in entries.items():
            if meta.get("trashed"):
                logger.warning("Skipping trashed Drive file %s (%r)", fid, meta.get("name"))
                continue

            mime = meta.get("mimeType") or ""
            name = meta.get("name") or fid
            exported_docx = False
            if mime == "application/vnd.google-apps.document":
                data = self._export_google_doc_docx(fid)
                exported_docx = True
            else:
                data = self._download_native_bytes(fid)

            fmt = _mime_to_import_format(mime, exported_docx=exported_docx)
            docs.append(
                ImportedDocument(
                    filename=name,
                    format=fmt,
                    data=data,
                    external_id=fid,
                    revision_id=str(meta.get("headRevisionId") or ""),
                    etag=str(meta.get("etag") or ""),
                )
            )

        return ImportResult(documents=docs, source_description=description)

    # ------------------------------------------------------------------ #
    # D4: Changes/Watch API                                                #
    # ------------------------------------------------------------------ #

    def get_start_page_token(self) -> str:
        """Return the current start page token for changes.list polling."""
        svc = self._svc()
        try:
            resp = svc.changes().getStartPageToken(
                supportsAllDrives=True,
            ).execute()
        except Exception as exc:
            _map_http_error(exc, context="Drive getStartPageToken")
        return str(resp.get("startPageToken", ""))

    def list_changes(self, page_token: str) -> dict:
        """Paginate through changes.list starting at *page_token*.

        Returns a dict with keys:
          - ``file_ids``: list of changed file IDs (excluding trashed-only changes)
          - ``new_start_page_token``: stable cursor when pagination is complete (empty while more pages exist)
          - ``next_page_token``: present when more pages are available
        """
        svc = self._svc()
        file_ids: list[str] = []
        current_token = page_token
        new_start_page_token = ""
        next_page_token = ""

        try:
            while True:
                resp = svc.changes().list(
                    pageToken=current_token,
                    spaces="drive",
                    fields="nextPageToken, newStartPageToken, changes(fileId, removed, file(trashed))",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()
                for change in resp.get("changes", []):
                    fid = change.get("fileId")
                    if not fid:
                        continue
                    removed = change.get("removed", False)
                    trashed = (change.get("file") or {}).get("trashed", False)
                    if not removed and not trashed:
                        file_ids.append(fid)
                next_page_token = resp.get("nextPageToken", "")
                new_start_page_token = resp.get("newStartPageToken", "")
                if not next_page_token:
                    break
                current_token = next_page_token
        except Exception as exc:
            _map_http_error(exc, context="Drive changes.list")

        return {
            "file_ids": file_ids,
            "new_start_page_token": new_start_page_token,
            "next_page_token": next_page_token,
        }

    def watch_changes(
        self,
        *,
        page_token: str,
        address: str,
        channel_id: str,
        channel_token: str,
        expiration_ms: int | None = None,
    ) -> dict:
        """Register a push-notification channel for changes.

        Returns the raw Google API response body including ``resourceId`` and
        ``expiration`` (epoch ms as string).
        """
        svc = self._svc()
        body: dict = {
            "id": channel_id,
            "type": "web_hook",
            "address": address,
            "token": channel_token,
        }
        if expiration_ms is not None:
            body["expiration"] = str(expiration_ms)
        try:
            resp = svc.changes().watch(
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                body=body,
            ).execute()
        except Exception as exc:
            _map_http_error(exc, context="Drive changes.watch")
        return dict(resp)

    def stop_channel(self, channel_id: str, resource_id: str) -> None:
        """Stop (unsubscribe) a push-notification channel."""
        svc = self._svc()
        body = {"id": channel_id, "resourceId": resource_id}
        try:
            svc.channels().stop(body=body).execute()
        except Exception as exc:
            _map_http_error(exc, context=f"Drive channels.stop {channel_id!r}")
