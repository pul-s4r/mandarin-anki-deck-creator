# Architecture Plan: Web Server Mode & Third-Party Integrations

## Table of Contents

1. [Current Architecture Summary](#1-current-architecture-summary)
2. [Core Library Refactoring](#2-core-library-refactoring)
3. [Web Server Mode](#3-web-server-mode)
4. [Third-Party Integration Framework](#4-third-party-integration-framework)
5. [Google Drive Integration (Case Study)](#5-google-drive-integration-case-study)
6. [Shared Concerns](#6-shared-concerns)
7. [Module Layout](#7-module-layout)
8. [Implementation Sequence](#8-implementation-sequence)

---

## 1. Current Architecture Summary

The project is a CLI pipeline with a clean linear flow:

```
CLI (cli.py)
  └── run_pipeline(input_path, output_csv, settings)
        ├── ingest/router.py   → extract_text_from_path()  [PDF / MD / DOCX → str]
        ├── preprocess/         → normalize_unicode, drop metadata lines, chunk_text
        ├── llm/bedrock_chain   → extract_vocabulary_from_chunk() per chunk
        ├── _dedupe_cards()
        ├── dictionary/enrich   → CEDICT enrichment (optional)
        └── export/csv_writer   → write_vocabulary_csv()
```

**Key properties of the current design:**

- Single-shot: reads a local file, processes synchronously, writes a CSV.
- Settings come from environment variables via `pydantic-settings` (`Settings`).
- Ingest router dispatches on file suffix; each ingestor takes a `Path` and returns `str`.
- The pipeline function (`run_pipeline`) owns the full lifecycle—there is no way to feed it an in-memory buffer, stream intermediate results, or track progress.

---

## 2. Core Library Refactoring

Before adding a web server or integrations, the core pipeline needs to be decoupled from filesystem I/O so both CLI and web server can share it without duplication.

### 2.1 Ingest layer: accept bytes, not just paths

Currently every ingestor takes a `Path`. Add parallel entry points that accept `bytes` + a MIME type / format hint so the web server and cloud-source integrations can feed data directly without writing temporary files.

```
# New signatures alongside existing ones
def extract_text_from_bytes(data: bytes, *, format: str) -> str
    """format is one of 'pdf', 'markdown', 'docx'."""
```

The existing `extract_text_from_path` becomes a thin wrapper that reads the file and delegates to the bytes-based function.

**Files changed:** `ingest/router.py`, `ingest/pdf.py`, `ingest/markdown.py`, `ingest/docx.py`.

### 2.2 Pipeline: separate orchestration from I/O

Split `run_pipeline` into composable stages that return intermediate data instead of writing directly to disk.

```python
@dataclass
class PipelineResult:
    rows: list[VocabularyRow]
    stats: PipelineStats          # chunk count, card count, enriched count, etc.

def run_pipeline_from_text(
    text: str,
    settings: Settings,
    *,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> PipelineResult:
    """Core pipeline: text in → rows out.  No filesystem I/O."""

def run_pipeline(input_path, output_csv, settings) -> None:
    """Original convenience wrapper—reads file, calls run_pipeline_from_text, writes CSV."""
```

The `progress_callback(stage, current, total)` hook lets the web server push status over WebSocket/SSE without the pipeline knowing anything about HTTP.

**Files changed:** `pipeline.py`.

### 2.3 Export: support in-memory output

`write_vocabulary_csv` currently writes to a `Path`. Add a companion that writes to an `io.StringIO` / returns `bytes`, so the web server can stream the response.

```python
def vocabulary_csv_bytes(rows, *, bom=False) -> bytes:
    ...
```

**Files changed:** `export/csv_writer.py`.

---

## 3. Web Server Mode

### 3.1 Technology choice

**FastAPI** is the natural fit:

- Already in the Python ecosystem (Pydantic models can be reused directly).
- Async support for long-running LLM calls.
- Built-in OpenAPI docs, file upload handling, and dependency injection.
- WebSocket support for progress streaming.

New dependency: `fastapi`, `uvicorn`, `python-multipart`.

### 3.2 Application structure

```
src/anki_deck_generator/
  web/
    __init__.py
    app.py              # FastAPI app factory
    dependencies.py     # Settings, DictionaryIndex, Bedrock model singletons
    routes/
      __init__.py
      pipeline.py       # POST /api/pipeline/run, GET /api/pipeline/{job_id}
      integrations.py   # POST /api/integrations/google-drive/import, etc.
      health.py         # GET /health
    schemas.py          # Pydantic request/response models
    jobs.py             # Background job tracking (in-memory, upgradeable to Redis)
```

### 3.3 API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/pipeline/run` | Upload a file, get back a job ID. Pipeline runs in a background task. |
| `GET` | `/api/pipeline/{job_id}` | Poll job status + download result when complete. |
| `GET` | `/api/pipeline/{job_id}/result` | Download the generated CSV. |
| `WS` | `/api/pipeline/{job_id}/ws` | Real-time progress updates (optional). |
| `POST` | `/api/integrations/{provider}/import` | Trigger an import from a 3rd-party source (see §4). |
| `GET` | `/health` | Liveness/readiness. |

### 3.4 Request/response models

```python
class PipelineRunRequest(BaseModel):
    """Multipart: file upload + optional JSON settings."""
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    csv_bom: bool = False
    skip_lines_filter: bool = True
    cedict_force_overwrite: bool = False

class PipelineJobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    progress: str | None = None        # e.g. "chunk 3/7"
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    download_url: str | None = None    # set when status == "completed"
```

### 3.5 Background job execution

Use FastAPI `BackgroundTasks` initially (single-process, in-memory). The job store is a simple `dict[str, PipelineJobStatus]` behind a lock, with results stored as in-memory bytes.

Future upgrade path: swap to Celery/ARQ + Redis for multi-worker deployments.

### 3.6 CLI entry point for the server

Add a new CLI sub-command:

```bash
anki-notes-pipeline serve --host 0.0.0.0 --port 8000
```

This starts Uvicorn programmatically with the FastAPI app.

**Files changed:** `cli.py` (add `serve` sub-command), new `web/` package.

### 3.7 CEDICT handling in server mode

The CEDICT dictionary is large (~120 MB parsed). In server mode it should be loaded once at startup and shared across requests via FastAPI dependency injection.

```python
# dependencies.py
@lru_cache
def get_dictionary_index(settings: Settings) -> DictionaryIndex | None:
    if settings.cedict_path and settings.cedict_path.is_file():
        return DictionaryIndex.from_source(FileLineDictionarySource(settings.cedict_path))
    return None
```

---

## 4. Third-Party Integration Framework

### 4.1 Design goals

1. **Uniform interface** — every integration implements the same protocol, whether invoked from CLI or web server.
2. **CLI parity** — any integration usable via `anki-notes-pipeline import <provider> ...` must also be triggerable via the web API, and vice versa.
3. **Incremental** — adding a new provider means implementing one class and registering it; no changes to the pipeline core.

### 4.2 Integration protocol

```python
# integrations/base.py

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

@dataclass
class ImportedDocument:
    """A single document fetched from an external source."""
    filename: str                   # original name, e.g. "lesson-notes.pdf"
    format: str                     # "pdf" | "markdown" | "docx" | "txt"
    data: bytes                     # raw file content

@dataclass
class ImportResult:
    """Outcome of an import operation."""
    documents: list[ImportedDocument]
    source_description: str         # human-readable, e.g. "Google Drive folder 'Chinese 301'"


class IntegrationProvider(ABC):
    """Base class for all external-source integrations."""

    name: str                       # e.g. "google-drive"

    @abstractmethod
    def authenticate(self, credentials: dict) -> None:
        """Set up authentication (OAuth tokens, API keys, etc.)."""
        ...

    @abstractmethod
    def list_sources(self, **kwargs) -> list[dict]:
        """List available documents/folders the user can import from."""
        ...

    @abstractmethod
    def import_documents(self, **kwargs) -> ImportResult:
        """Fetch one or more documents and return them as ImportedDocument objects."""
        ...
```

### 4.3 Provider registry

```python
# integrations/registry.py

_PROVIDERS: dict[str, type[IntegrationProvider]] = {}

def register(name: str, cls: type[IntegrationProvider]):
    _PROVIDERS[name] = cls

def get_provider(name: str) -> IntegrationProvider:
    return _PROVIDERS[name]()

def available_providers() -> list[str]:
    return list(_PROVIDERS.keys())
```

Providers self-register at import time via a decorator or explicit call.

### 4.4 Integration in CLI mode

New sub-command:

```bash
anki-notes-pipeline import google-drive \
    --folder-id <FOLDER_ID> \
    --credentials-file /path/to/service-account.json \
    --output out.csv \
    --cedict-path /path/to/cedict_ts.u8
```

Flow:
1. Instantiate provider, call `authenticate()`.
2. Call `import_documents()` → list of `ImportedDocument`.
3. For each document, call `extract_text_from_bytes(doc.data, format=doc.format)`.
4. Concatenate texts (or run pipeline per-document), then continue with the standard pipeline.

### 4.5 Integration in web server mode

```
POST /api/integrations/google-drive/import
{
    "folder_id": "...",
    "file_ids": ["...", "..."],            // alternative to folder_id
    "pipeline_settings": { ... }           // optional overrides
}
Authorization: Bearer <oauth-token>        // or stored in session
```

The endpoint:
1. Resolves the provider from the URL path.
2. Calls `authenticate()` with the token from the request.
3. Calls `import_documents()`.
4. Creates a background pipeline job for each document (or a batch job for all).
5. Returns job IDs.

### 4.6 Credential handling

| Mode | Approach |
|------|----------|
| CLI | `--credentials-file` flag pointing to a JSON key file, or environment variables (`GOOGLE_APPLICATION_CREDENTIALS`, etc.). |
| Web server | OAuth 2.0 flow. The web app initiates the OAuth redirect; tokens are stored per-session (in-memory or in a lightweight DB like SQLite). For service-account based access, credentials come from server-side config. |

---

## 5. Google Drive Integration (Case Study)

### 5.1 Dependencies

- `google-api-python-client`
- `google-auth-oauthlib` (for OAuth in web mode)
- `google-auth` (for service account in CLI mode)

These are **optional** dependencies, guarded behind an extras group:

```toml
[project.optional-dependencies]
google-drive = ["google-api-python-client>=2", "google-auth>=2", "google-auth-oauthlib>=1"]
```

### 5.2 Implementation

```python
# integrations/google_drive.py

class GoogleDriveProvider(IntegrationProvider):
    name = "google-drive"

    def authenticate(self, credentials: dict) -> None:
        # credentials may contain:
        #   - "service_account_file": path to SA JSON (CLI mode)
        #   - "oauth_token": access token (web mode)
        ...

    def list_sources(self, *, folder_id: str | None = None) -> list[dict]:
        # Use Drive API v3 files.list, filter mimeType for supported types
        # Google Docs → export as DOCX; Google Sheets → skip or export as CSV
        ...

    def import_documents(self, *, folder_id: str | None = None,
                         file_ids: list[str] | None = None) -> ImportResult:
        # Download each file as bytes
        # For Google Docs: use export endpoint with DOCX mime type
        # For native PDF/DOCX: use media download
        # For Google Docs text: export as plain text → format="txt"
        ...
```

### 5.3 Google Docs handling

Google Docs are not traditional files. The integration should:

1. Detect `mimeType == "application/vnd.google-apps.document"`.
2. Export via `files.export(fileId, mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document")` to get DOCX bytes.
3. Alternatively, export as `text/plain` for simpler notes.
4. Mark `format="docx"` or `format="txt"` accordingly on the `ImportedDocument`.

### 5.4 CLI usage

```bash
# Service account mode
anki-notes-pipeline import google-drive \
    --credentials-file sa-key.json \
    --folder-id 1aBcDeFgHiJ \
    --output vocab.csv

# Individual files
anki-notes-pipeline import google-drive \
    --credentials-file sa-key.json \
    --file-id 1xYz2AbC \
    --file-id 2qRs3TuV \
    --output vocab.csv
```

### 5.5 Web server usage

1. User clicks "Import from Google Drive" in the (future) frontend.
2. App redirects to Google OAuth consent screen with `drive.readonly` scope.
3. Callback stores the access token in the session.
4. Frontend shows a file/folder picker (built on `list_sources()`).
5. User selects files → `POST /api/integrations/google-drive/import`.
6. Server downloads, runs pipeline, returns job IDs.

---

## 6. Shared Concerns

### 6.1 Error handling

Both modes need structured error reporting. Define a hierarchy:

```python
class AnkiPipelineError(Exception): ...
class IngestError(AnkiPipelineError): ...
class LlmError(AnkiPipelineError): ...
class IntegrationError(AnkiPipelineError): ...
class AuthenticationError(IntegrationError): ...
```

The CLI catches these and prints human-readable messages. The web server catches them and returns JSON error responses with appropriate HTTP status codes.

### 6.2 Async execution

The LLM calls (`extract_vocabulary_from_chunk`) are the bottleneck. In web mode, these should run in a thread pool executor to avoid blocking the event loop. The existing synchronous Bedrock client works fine inside `asyncio.to_thread()`.

Future: if LangChain's async Bedrock support matures, switch to native async invocation and process chunks concurrently (with rate limiting).

### 6.3 Configuration

`Settings` already uses `pydantic-settings`, which reads from environment variables and `.env` files. This works for both CLI and web server without changes. Server-specific settings (host, port, CORS origins, etc.) get their own `ServerSettings` class.

```python
class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANKI_SERVER_")
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]
    max_upload_size_mb: int = 50
```

### 6.4 Testing strategy

- **Unit tests** for the bytes-based ingestors (existing tests adapt easily).
- **Integration tests** for the web API using FastAPI's `TestClient`.
- **Mock-based tests** for integration providers (mock the Google API client).
- The existing `test_pipeline_e2e_mocked.py` pattern extends to test the new `run_pipeline_from_text` directly.

### 6.5 CORS and security (web mode)

- CORS middleware configured via `ServerSettings.cors_origins`.
- File upload size limits enforced at the FastAPI level.
- Rate limiting on the pipeline endpoint (optional, via `slowapi` or similar).
- No authentication by default for local use; add optional API key or OAuth middleware for deployment.

---

## 7. Module Layout

```
src/anki_deck_generator/
├── __init__.py
├── cli.py                          # Extended: add 'serve' and 'import' sub-commands
├── pipeline.py                     # Refactored: run_pipeline_from_text + PipelineResult
├── config/
│   ├── __init__.py
│   └── settings.py                 # Add ServerSettings
├── ingest/
│   ├── __init__.py
│   ├── router.py                   # Add extract_text_from_bytes()
│   ├── pdf.py                      # Add bytes-based variant
│   ├── markdown.py                 # Add bytes-based variant
│   └── docx.py                     # Add bytes-based variant
├── preprocess/
│   ├── __init__.py
│   ├── normalize.py
│   └── chunk.py
├── llm/
│   ├── __init__.py
│   ├── schemas.py
│   └── bedrock_chain.py
├── dictionary/
│   ├── __init__.py
│   ├── parser.py
│   ├── index.py
│   ├── source.py
│   ├── enrich.py
│   └── pinyin_normalize.py
├── export/
│   ├── __init__.py
│   └── csv_writer.py              # Add vocabulary_csv_bytes()
├── integrations/                   # NEW
│   ├── __init__.py
│   ├── base.py                    # IntegrationProvider ABC, ImportedDocument, ImportResult
│   ├── registry.py                # Provider registry
│   └── google_drive.py            # Google Drive provider
├── web/                            # NEW
│   ├── __init__.py
│   ├── app.py                     # FastAPI app factory
│   ├── dependencies.py            # DI: Settings, DictionaryIndex, Bedrock model
│   ├── jobs.py                    # Background job store
│   ├── schemas.py                 # API request/response models
│   └── routes/
│       ├── __init__.py
│       ├── pipeline.py            # /api/pipeline/*
│       ├── integrations.py        # /api/integrations/*
│       └── health.py              # /health
└── errors.py                      # NEW: structured exception hierarchy
```

---

## 8. Implementation Sequence

The work decomposes into four phases. Each phase is self-contained and produces a working, testable state.

### Phase 1 — Core refactoring (prerequisite for everything else)

| Step | Change | Risk |
|------|--------|------|
| 1a | Add bytes-based ingest functions (`extract_text_from_bytes` etc.) | Low — additive, existing tests still pass. |
| 1b | Extract `run_pipeline_from_text` from `run_pipeline`; add `PipelineResult` and progress callback | Medium — must preserve exact current behavior. |
| 1c | Add `vocabulary_csv_bytes()` in export | Low — additive. |
| 1d | Add `errors.py` exception hierarchy | Low — additive. |

### Phase 2 — Web server

| Step | Change | Risk |
|------|--------|------|
| 2a | Add `web/` package with FastAPI app, health endpoint | Low. |
| 2b | Implement `/api/pipeline/run` with file upload + background job | Medium — thread pool + job store. |
| 2c | Implement `/api/pipeline/{job_id}` status + result download | Low. |
| 2d | Add `serve` sub-command to CLI | Low. |
| 2e | Add `ServerSettings`, CORS, upload limits | Low. |
| 2f | WebSocket progress streaming (optional) | Low — nice-to-have. |

### Phase 3 — Integration framework

| Step | Change | Risk |
|------|--------|------|
| 3a | Create `integrations/base.py` with ABC and data classes | Low. |
| 3b | Create `integrations/registry.py` | Low. |
| 3c | Add `import` sub-command to CLI that dispatches to providers | Low. |
| 3d | Add `/api/integrations/{provider}/import` web route | Low — reuses provider protocol. |

### Phase 4 — Google Drive provider

| Step | Change | Risk |
|------|--------|------|
| 4a | Implement `GoogleDriveProvider` with service-account auth for CLI | Medium — depends on Google API. |
| 4b | Add OAuth flow for web mode (callback endpoint, token storage) | Medium — OAuth state management. |
| 4c | File picker API (`list_sources`) | Low. |
| 4d | Add optional dependency group `[google-drive]` to `pyproject.toml` | Low. |

### Future providers (not in scope, but follow the same pattern)

- **Notion** — export pages as Markdown via Notion API.
- **Dropbox** — download files via Dropbox API.
- **OneNote** — export via Microsoft Graph API.
- **Direct URL** — fetch a file from a URL (simplest possible provider).
