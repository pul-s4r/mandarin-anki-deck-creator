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
9. [Expanded Use Cases (Scheduled / Event-Driven Operation)](#9-expanded-use-cases-scheduled--event-driven-operation)
10. [Deployment Target Selection](#10-deployment-target-selection)
11. [Scheduled & Triggered Execution](#11-scheduled--triggered-execution)
12. [Incremental Processing & Persistent Memory](#12-incremental-processing--persistent-memory)
13. [Export Targets — XLSX and AnkiWeb](#13-export-targets--xlsx-and-ankiweb)
14. [Revised Module Layout](#14-revised-module-layout)
15. [Revised Implementation Sequence](#15-revised-implementation-sequence)
16. [Open Questions & Risks](#16-open-questions--risks)

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

---

## 9. Expanded Use Cases (Scheduled / Event-Driven Operation)

The original plan (§§1–8) covers a user-initiated model: a human uploads a file via CLI or HTTP, the pipeline runs once, and a CSV comes back. The expanded scope below turns the pipeline into a long-lived service that tracks a corpus of source documents over time and keeps a deck in sync with them, with minimal human input and minimal always-on infrastructure.

### 9.1 Use cases to support

| # | Use case | Trigger | Expected behavior |
|---|----------|---------|-------------------|
| U1 | Weekly refresh of a "study notes" Google Drive folder | Cron (e.g. `Fri 23:59:59 local`) | Scan configured sources, process only what changed, append to deck. |
| U2 | "I just edited a doc, update my deck now" | Google Drive push notification (webhook) or manual trigger | Same as U1 but scoped to one document. |
| U3 | Manual one-shot run (existing CLI flow) | Human | Unchanged from today. |
| U4 | Export everything accumulated so far | Human or schedule | Emit a full CSV / XLSX / AnkiWeb sync of the persistent deck state. |
| U5 | Push new/changed cards to AnkiWeb automatically | After any pipeline run | New/modified cards appear on the user's AnkiWeb account without manual CSV import. |

### 9.2 Non-functional requirements

- **Intermittent compute**: no resource should be "always on" unless run frequency or payload size justifies it. The default deployment shape must be serverless / scale-to-zero.
- **Idempotency**: re-running a trigger on the same inputs must not produce duplicate cards on AnkiWeb or in exported files.
- **Durability across runs**: all state the pipeline needs (document revisions, per-chunk hashes, card inventory, AnkiWeb sync cursors, OAuth tokens) must survive between invocations on ephemeral compute.
- **Observability**: every scheduled run must produce a structured log/summary the user can inspect (docs scanned, docs changed, chunks processed, cards added/updated/skipped, AnkiWeb sync result).
- **Safety**: a failed run must not corrupt persistent memory or leave AnkiWeb in a half-synced state; retries must be safe.

### 9.3 Relationship to the §§1–8 plan

The original plan stays intact. Sections 9–15 add three orthogonal capabilities on top of the web/CLI modes:

1. A **serverless entry point** (AWS Lambda) that can host the same `run_pipeline_from_text` core as the FastAPI app.
2. A **persistence + change-tracking layer** that lets any entry point (CLI, web, Lambda) ingest only what's new.
3. Two **new export targets** — XLSX and AnkiWeb — that plug into the export layer alongside CSV.

None of these require abandoning FastAPI/CLI; they share code with them.

---

## 10. Deployment Target Selection

### 10.1 Candidate shapes

| Option | Always-on cost | Fits U1 (weekly cron)? | Fits U2 (webhook)? | Notes |
|--------|----------------|-----------------------|--------------------|-------|
| A. Local cron + existing CLI | $0 | Yes, if laptop is on | No (no public URL) | Acceptable for solo personal use; fails the "runs from anywhere on schedule" goal. |
| B. Long-running EC2 / container with APScheduler | High (24/7) | Yes | Yes | Violates the "no constantly-on resources" requirement. |
| C. **AWS Lambda + EventBridge Scheduler + API Gateway (HTTP API)** | Pennies/month | Yes (EventBridge cron) | Yes (API Gateway → Lambda) | Scale-to-zero. Natural fit for Bedrock (same account). Primary recommendation. |
| D. Google Cloud Run Jobs + Cloud Scheduler | Pennies/month | Yes | Yes | Viable if Bedrock access migrated; adds a second cloud. Secondary option. |
| E. GitHub Actions scheduled workflow | $0 for personal repos (within free minutes) | Yes | Awkward (webhooks land via `repository_dispatch`) | Viable fallback; cold starts less of an issue because cron-only. |

**Recommendation: Option C (AWS Lambda).** It satisfies every non-functional requirement, keeps credentials for Bedrock in the same account, and scales to zero between runs. Options A and E are documented as fallback modes so the code does not require AWS to run.

### 10.2 Lambda packaging strategy

The existing codebase has two heavy dependencies that affect Lambda sizing:

- `boto3` / `langchain-aws` for Bedrock (already required).
- Potentially `python-docx`, `pypdf`, and the CEDICT index (~120 MB parsed).

To keep package size manageable:

- Ship the Lambda as a **container image** (up to 10 GB, vs. 250 MB zip). This sidesteps the CEDICT size problem entirely and lets us reuse a single image per entry point.
- Base image: `public.ecr.aws/lambda/python:3.12`.
- Store CEDICT in **EFS mounted on the Lambda** or in **S3**, loaded lazily on cold start. EFS is simpler (filesystem semantics, no manual download) and its always-on cost is negligible at the sizes we need; S3 + local cache in `/tmp` is cheaper still but requires a parser cold-start each time.
- Decision defaulted to **S3 + `/tmp` cache** because CEDICT is write-once and the cold-start cost of reading ~120 MB from S3 is acceptable for a weekly-ish schedule. EFS stays listed as a fallback.

### 10.3 Shared code shape across entry points

The goal is that the following are all thin shims around the same core:

```
FastAPI route handler  ─┐
CLI `run` sub-command  ─┤──► run_pipeline_from_text (pure)
CLI `import` command   ─┤
Lambda handler         ─┘
```

The Lambda handler adds only: (a) event parsing (EventBridge vs. API Gateway vs. SNS), (b) settings loaded from environment + SSM Parameter Store / Secrets Manager, (c) persistent-state wiring (see §12), (d) export dispatch (see §13).

### 10.4 Local development parity

Every piece deployed to Lambda must also run locally with no AWS involvement:

- EventBridge schedule → replaced by a `schedule` sub-command on the CLI that runs the same handler once (`anki-notes-pipeline schedule --source <name>`).
- API Gateway webhook → replaced by FastAPI routes (§3) that call the same handler.
- DynamoDB state store → replaced by a SQLite-backed implementation behind the same interface (§12).

This keeps the "no AWS required for dev" invariant from §6 and makes unit testing cheap.

---

## 11. Scheduled & Triggered Execution

### 11.1 Trigger taxonomy

| Trigger | Source | Payload (conceptual) | Entry point |
|---------|--------|----------------------|-------------|
| T1: Cron | EventBridge Scheduler | `{ "source_set": "weekly-chinese-notes" }` | Lambda handler `handle_schedule` |
| T2: Drive push notification | Google Drive `changes.watch` webhook → API Gateway | Drive channel headers + resource state | Lambda handler `handle_drive_webhook` |
| T3: Manual API | FastAPI `/api/integrations/google-drive/import` or CLI | File IDs / folder IDs | Shared `run_sync(...)` function |
| T4: Dead-letter / retry | SQS DLQ replay | Original event | Same as originating handler |

All four funnel into one internal function:

```python
def run_sync(
    *,
    source_set: SourceSet,
    settings: Settings,
    only_file_ids: list[str] | None = None,   # for T2/T3 targeted runs
    state_store: StateStore,
    exporters: list[Exporter],
) -> SyncReport: ...
```

`SyncReport` carries counts, per-document results, and export results so every trigger can produce a uniform log entry.

### 11.2 EventBridge Scheduler (T1)

- One schedule per "source set". A source set is a named bundle of integration configs (e.g. `weekly-chinese-notes` → Google Drive folder X + file Y on cron `cron(59 23 ? * FRI *)`).
- Schedule configuration lives in source code / IaC (see §15 phase 8), not hand-edited in the AWS console, so it's reproducible.
- Schedules invoke the Lambda directly (no SQS hop needed for cron; cron is already idempotent at our volumes).

### 11.3 Google Drive change notifications (T2)

Drive supports webhook-style push via `changes.watch`. Flow:

1. At setup, call `changes.getStartPageToken` and store it as the initial cursor in `StateStore` (§12).
2. Call `changes.watch(pageToken, address=<API Gateway URL>, expiration=...)` to register a push channel. Drive channels expire (max ~7 days), so a nightly EventBridge schedule renews them. Channel metadata (id, resourceId, expiration) lives in `StateStore`.
3. On webhook delivery, the Lambda reads the stored `pageToken`, calls `changes.list` to get the actual diff, enqueues affected file IDs into the same `run_sync` path with `only_file_ids=[...]`, then advances the cursor.
4. Drive webhooks carry no body — just headers (`X-Goog-Channel-ID`, `X-Goog-Resource-State`). The handler is just a thin dispatcher; the real work is the `changes.list` call.

### 11.4 Idempotency and retries

- API Gateway → Lambda can retry on 5xx. Handlers must be safe to run twice on the same input. §12 (persistent memory) ensures this at the data layer; the handler layer enforces it by making all writes keyed on content hashes and Drive revision IDs.
- A Lambda **dead-letter SQS queue** captures terminal failures for manual replay. Alarms on DLQ depth are the "something broke" signal.

### 11.5 Scheduling for local/GitHub Actions fallback

- Local: `cron` invokes `anki-notes-pipeline schedule --source-set weekly-chinese-notes` which is the exact function the Lambda calls.
- GitHub Actions: a `.github/workflows/weekly-sync.yml` on `schedule:` runs the same command inside the container image used for Lambda, so behavior is identical. Secrets come from repo secrets instead of AWS Secrets Manager; the settings loader abstracts this.

---

## 12. Incremental Processing & Persistent Memory

This is the keystone of the new design. Without it, the pipeline either reprocesses every document every run (expensive and non-deterministic thanks to the LLM) or it drops changes silently.

### 12.1 What needs to persist

| Category | Example fields | Access pattern | Size (order of magnitude) |
|----------|---------------|----------------|---------------------------|
| Source document state | `source_id`, `provider`, `external_id`, `revision_id`, `etag`, `content_sha256`, `last_ingested_at` | Lookup by `(provider, external_id)` | 10s–100s of rows |
| Chunk state | `source_id`, `chunk_index`, `chunk_sha256`, `processed_at`, `model_id`, `llm_output_card_ids` | Lookup by `(source_id, chunk_index)` | 1k–10k rows |
| Card inventory (the deck) | `card_id` (stable), `simplified` (natural key), `traditional`, `pinyin`, `meaning`, `part_of_speech`, `usage_notes`, `first_seen_source_id`, `last_updated_at`, `content_hash`, `ankiweb_note_id`, `ankiweb_last_synced_at` | Lookup by `simplified`; scan for "changed since X" | 1k–10k rows |
| Drive channel / cursor state | `channel_id`, `resource_id`, `page_token`, `expiration` | Singleton-ish | <10 rows |
| OAuth tokens & secrets | refresh tokens, encrypted | Per-provider | Handful |
| Run history | `run_id`, `trigger`, `started_at`, `finished_at`, `sync_report_json` | Time-ordered | 100s/year |

### 12.2 Storage choice

Requirements:

- Survives Lambda restarts (so not in-memory).
- Pay-per-request / scale-to-zero (so not RDS).
- Atomic upsert on a key (for the card inventory).
- Queryable by secondary attributes at small scale.

**Primary pick: DynamoDB (on-demand billing).**

- Scale-to-zero fits the "no always-on" rule.
- Single-digit-ms reads from Lambda in the same region.
- Conditional writes give us the idempotency guarantees we need for card upserts.

**Local/dev pick: SQLite file** accessed through the same abstraction, so tests don't need DynamoDB Local.

### 12.3 Abstraction

```python
# state/store.py

class StateStore(Protocol):
    # Source documents
    def get_source_record(self, provider: str, external_id: str) -> SourceRecord | None: ...
    def upsert_source_record(self, rec: SourceRecord) -> None: ...

    # Chunks
    def get_processed_chunk(self, source_id: str, chunk_index: int) -> ChunkRecord | None: ...
    def upsert_processed_chunk(self, rec: ChunkRecord) -> None: ...

    # Cards (deck inventory)
    def get_card_by_key(self, natural_key: str) -> CardRecord | None: ...
    def upsert_card(self, rec: CardRecord) -> CardUpsertResult: ...      # returns {created, updated, unchanged}
    def iter_cards_changed_since(self, ts: datetime) -> Iterable[CardRecord]: ...

    # Drive cursors
    def get_drive_channel(self, channel_id: str) -> DriveChannelRecord | None: ...
    def upsert_drive_channel(self, rec: DriveChannelRecord) -> None: ...

    # Run history
    def record_run(self, report: SyncReport) -> None: ...
```

Two concrete implementations: `DynamoStateStore`, `SqliteStateStore`. Selection is by config (`ANKI_PIPELINE_STATE_BACKEND=dynamodb|sqlite`).

### 12.4 Change detection strategy

Layered, cheap → expensive:

1. **Document level.** For each configured source, ask the provider for its current revision/etag. Compare to `SourceRecord.revision_id`. If unchanged, skip; don't even download.
2. **Content level.** If the provider doesn't expose a stable revision (or to double-check), hash the downloaded bytes (`content_sha256`). If unchanged, skip.
3. **Chunk level.** When a document *has* changed, re-chunk it and hash each chunk. Only chunks whose `chunk_sha256` is new go through the LLM. This is the single biggest cost optimization: a one-line edit in a long doc should not re-LLM 20 chunks.
4. **Card level.** Upsert by natural key (`simplified`). Only mark a card "changed" if `content_hash` differs from the stored one; otherwise upsert is a no-op. This is what makes AnkiWeb sync deterministic.

### 12.5 Pipeline wiring

Core pipeline gains a variant that is persistence-aware:

```python
def run_incremental_sync(
    source_set: SourceSet,
    *,
    settings: Settings,
    state_store: StateStore,
    exporters: list[Exporter],
    only_file_ids: list[str] | None = None,
) -> SyncReport:
    for source in source_set.resolve(state_store, only_file_ids=only_file_ids):
        if not source.changed_since_last_run():
            report.skipped.append(source.id); continue
        text = extract_text_from_bytes(source.data, format=source.format)
        text = normalize_unicode(text)
        text = optional_drop_metadata_lines(text, enabled=settings.skip_lines_filter)
        new_chunks = select_unprocessed_chunks(text, source, state_store, settings)
        cards = run_llm_over_chunks(new_chunks, settings)
        upsert_results = [state_store.upsert_card(to_card_record(c, source)) for c in cards]
        mark_chunks_processed(new_chunks, state_store)
        state_store.upsert_source_record(source.to_record())
        report.add(source, upsert_results)
    for exporter in exporters:
        report.exports.append(exporter.export(state_store, since=report.run_started_at))
    state_store.record_run(report)
    return report
```

Key properties:

- Existing `run_pipeline_from_text` is reused for the "text → cards" substep (no duplication).
- The incremental layer is a strict superset: if `StateStore` is empty, it behaves like a full run.
- Exporters consume from `StateStore`, not from in-memory pipeline state, so exports are always consistent with what was persisted (no partial-success CSV).

### 12.6 CEDICT enrichment in this model

Today, enrichment runs inside `run_pipeline_from_text`. Two options going forward:

- **Keep it inside the pipeline** (current behavior). Cheapest for small documents; fine.
- **Run it as an enrichment pass over the card inventory** after upsert, only for cards whose `enrichment_version` is older than the current CEDICT version. Useful when CEDICT updates or when we add new enrichment fields; avoids re-running the LLM just to refresh a translation.

Recommendation: start with option 1 (no change) and leave option 2 as a follow-up; `StateStore` is designed to support it via an `enrichment_version` column on `CardRecord`.

### 12.7 Data lifecycle

- `SourceRecord`, `ChunkRecord`: retained indefinitely (cheap; allows "why is this chunk in the deck?" audits).
- `CardRecord`: retained indefinitely. Soft-delete flag (`retired_at`) instead of hard delete, since AnkiWeb has its own notion of deletion.
- Run history: TTL 90 days (DynamoDB TTL attribute) to avoid unbounded growth.
- OAuth refresh tokens: stored in AWS Secrets Manager rather than DynamoDB; handler pulls at cold start.

---

## 13. Export Targets — XLSX and AnkiWeb

### 13.1 Exporter protocol

```python
# export/base.py

class Exporter(Protocol):
    name: str                                    # "csv" | "xlsx" | "ankiweb"

    def export(
        self,
        state_store: StateStore,
        *,
        since: datetime | None = None,           # None → full export
    ) -> ExportResult: ...
```

`ExportResult` carries counts (created / updated / unchanged / failed), artifact URIs (for file exports), and provider-specific metadata (e.g. AnkiWeb sync timestamp).

The existing `write_vocabulary_csv` becomes the body of `CsvExporter.export`. Same for a new `vocabulary_csv_bytes` (already in §2.3).

### 13.2 XLSX export

- New optional dependency: `openpyxl` (behind extras group `[xlsx]`).
- Schema: same columns as CSV, plus optional metadata sheet (`Run metadata`, `Source documents`) that includes the SyncReport summary — useful when a human wants to audit a given run.
- File written to:
  - Local path (CLI mode).
  - Presigned S3 URL (Lambda mode). Bucket configured via `ANKI_PIPELINE_EXPORT_S3_BUCKET`.
  - In-memory bytes (web mode; streamed as download).

### 13.3 AnkiWeb export

AnkiWeb is the trickiest integration because its official external API is limited. Two viable approaches:

#### 13.3.1 Option A — AnkiConnect bridge (preferred for local / self-hosted)

- The user runs the [AnkiConnect](https://foosoft.net/projects/anki-connect/) add-on on a desktop Anki install.
- Our exporter POSTs JSON-RPC calls (`addNotes`, `updateNoteFields`, `findNotes`) to a local URL (e.g. `http://127.0.0.1:8765`).
- Desktop Anki then syncs to AnkiWeb.
- Pros: officially sanctioned, rich API, stable, no scraping.
- Cons: requires desktop Anki to be running during sync.

#### 13.3.2 Option B — AnkiWeb session-cookie client (fallback, no desktop)

- Log into AnkiWeb via form POST with username/password, maintain the session cookie.
- Use undocumented endpoints (CSV import, note add). Brittle; terms-of-service considerations apply.
- Listed as a fallback only; default is Option A.

#### 13.3.3 Option C — Hybrid: bring-your-own Anki on a schedule

- Lambda cannot reach a home-LAN AnkiConnect instance directly. Solution: a tiny **pull-based agent** (a script run by launchd / systemd on the user's desktop) fetches a delta feed from our service (`GET /api/ankiweb/pending?since=...`), applies it via AnkiConnect, and POSTs the result back (`POST /api/ankiweb/ack`).
- Our Lambda never initiates an outbound connection to the user's machine; all sync is driven from the desktop.
- Requires the user's machine to be on and Anki running at some point after a sync, but not during it.

Recommendation: implement **Option C** because it matches the "no always-on resources" rule and sidesteps firewall/NAT issues, while still giving the user automatic AnkiWeb sync whenever their desktop comes online. Keep Option B as a manual CSV fallback for users who can't run the agent.

### 13.4 AnkiWeb exporter responsibilities

Regardless of option:

- Operate on `CardRecord`s with `ankiweb_last_synced_at is None or < last_updated_at`.
- Maintain `ankiweb_note_id` and `ankiweb_last_synced_at` in `StateStore` after successful sync.
- Report per-card outcome in `ExportResult` so a user can see "added 3, updated 1, skipped 47".
- Handle duplicate detection (AnkiConnect rejects duplicate first-field notes by default; our exporter handles this by falling back to `updateNoteFields`).

### 13.5 Exporter composition

Source sets declare which exporters fire:

```yaml
source_sets:
  weekly-chinese-notes:
    sources:
      - provider: google-drive
        folder_id: "1aBcDeFgHiJ"
    schedule: "cron(59 23 ? * FRI *)"
    exporters:
      - type: csv
        destination: s3://my-bucket/decks/chinese-latest.csv
      - type: xlsx
        destination: s3://my-bucket/decks/chinese-latest.xlsx
      - type: ankiweb
        deck_name: "Chinese::301"
        note_type: "Chinese vocabulary"
```

This config is the same whether loaded by the CLI, the web server, or the Lambda.

---

## 14. Revised Module Layout

Superset of §7; new directories marked **NEW**, modified ones marked **MOD**.

```
src/anki_deck_generator/
├── __init__.py
├── cli.py                          # MOD: adds `serve`, `import`, `schedule`, `export` sub-commands
├── pipeline.py                     # MOD: run_pipeline_from_text + run_incremental_sync
├── config/
│   ├── __init__.py
│   ├── settings.py                 # MOD: ServerSettings + LambdaSettings + SourceSet loader
│   └── source_sets.py              # NEW: YAML/py loader for §13.5 configs
├── ingest/ ...                     # (unchanged from §7)
├── preprocess/ ...                 # (unchanged)
├── llm/ ...                        # (unchanged)
├── dictionary/ ...                 # (unchanged)
├── export/
│   ├── __init__.py
│   ├── base.py                     # NEW: Exporter protocol + ExportResult
│   ├── csv_writer.py               # MOD: wrapped into CsvExporter
│   ├── xlsx_writer.py              # NEW: XlsxExporter
│   └── ankiweb/                    # NEW
│       ├── __init__.py
│       ├── anki_connect.py         # Option A (§13.3.1)
│       ├── session_client.py       # Option B fallback (§13.3.2)
│       └── pull_agent_api.py       # Option C hybrid server-side half (§13.3.3)
├── integrations/ ...               # (as in §7) + google_drive gains `get_revision`, `watch_changes`
├── state/                          # NEW
│   ├── __init__.py
│   ├── records.py                  # SourceRecord, ChunkRecord, CardRecord, DriveChannelRecord
│   ├── store.py                    # StateStore protocol
│   ├── dynamo_store.py             # DynamoDB impl
│   └── sqlite_store.py             # SQLite impl (dev/local)
├── sync/                           # NEW
│   ├── __init__.py
│   ├── orchestrator.py             # run_incremental_sync (§12.5)
│   ├── change_detection.py         # doc/content/chunk diffing (§12.4)
│   └── report.py                   # SyncReport
├── web/ ...                        # (as in §3) + /api/sync/* and /api/ankiweb/* routes
├── lambda/                         # NEW
│   ├── __init__.py
│   ├── handler_schedule.py         # T1 entry
│   ├── handler_drive_webhook.py    # T2 entry
│   ├── handler_api.py              # API Gateway shim around FastAPI (via Mangum or direct)
│   └── bootstrap.py                # shared cold-start wiring (CEDICT, settings, StateStore)
├── infra/                          # NEW (deployment only, no runtime code)
│   ├── README.md
│   ├── lambda.Dockerfile
│   └── cdk/ or sam/                # whichever IaC we pick in phase 8
└── errors.py                       # (as in §7)
```

Notes:

- `sync/` is a new layer that sits between `integrations/` (fetchers) and the existing `pipeline.py` core.
- `lambda/` contains *only* handlers; all real logic lives in shared modules so tests don't need AWS.
- `infra/` is intentionally separated from runtime code so it can be excluded from the Python package build.

---

## 15. Revised Implementation Sequence

Phases 1–4 from §8 stay unchanged and remain prerequisites. The new work is phases 5–8.

### Phase 5 — Persistent state layer

| Step | Change | Risk |
|------|--------|------|
| 5a | Define `state/records.py` dataclasses. | Low. |
| 5b | Define `StateStore` protocol in `state/store.py`. | Low. |
| 5c | Implement `SqliteStateStore` + unit tests (in-memory DB). | Low. |
| 5d | Wire a `--state-db` flag into CLI; add a `state` subcommand for inspection (`state list-cards`, `state list-runs`). | Low. |
| 5e | Implement `DynamoStateStore` behind the same protocol; integration-tested against [moto](https://github.com/getmoto/moto). | Medium — access patterns must match DynamoDB's single-table idioms. |

### Phase 6 — Incremental sync orchestrator

| Step | Change | Risk |
|------|--------|------|
| 6a | Build `sync/change_detection.py` (document/content/chunk diffing). | Medium — chunk hashing must be stable under text normalization. |
| 6b | Build `sync/orchestrator.run_incremental_sync` using existing `run_pipeline_from_text` for the LLM substep. | Medium — ensure exact behavioral parity with today's pipeline when state store is empty. |
| 6c | Add `schedule` CLI sub-command that loads a `SourceSet` config and invokes the orchestrator locally. | Low. |
| 6d | Extend Google Drive provider with `get_revision()` and `changes.list` helpers. | Medium — Drive API quirks, token expiry. |
| 6e | End-to-end test: two runs over the same folder, with a single edit between them → second run processes only the edited chunks. | Medium. |

### Phase 7 — New export targets

| Step | Change | Risk |
|------|--------|------|
| 7a | Introduce `export/base.py` Exporter protocol; wrap existing CSV writer as `CsvExporter`. | Low. |
| 7b | Implement `XlsxExporter` with optional `[xlsx]` extras. | Low. |
| 7c | Implement AnkiConnect client (`export/ankiweb/anki_connect.py`) + exporter that diffs against `StateStore`. | Medium — AnkiConnect duplicate handling and note-type schema mapping. |
| 7d | Implement pull-agent endpoints (`/api/ankiweb/pending`, `/api/ankiweb/ack`) and a sample desktop agent script under `scripts/ankiweb-pull-agent/`. | Medium — need robust idempotency via `ack` tokens. |
| 7e | (Optional / fallback) Implement session-cookie AnkiWeb client. | High — unofficial endpoints; treat as experimental and gate behind a config flag. |

### Phase 8 — Serverless deployment

| Step | Change | Risk |
|------|--------|------|
| 8a | Add `lambda/bootstrap.py` that assembles `Settings`, `StateStore`, and loads CEDICT from S3 into `/tmp` (cached across warm invocations). | Medium — cold-start budget. |
| 8b | Implement `handler_schedule` (T1) delegating to `run_incremental_sync`. | Low. |
| 8c | Implement `handler_drive_webhook` (T2) using stored Drive cursor. | Medium — channel lifecycle. |
| 8d | Add `handler_api` for API Gateway → FastAPI via Mangum (reuses §3 routes unchanged). | Low. |
| 8e | Build `infra/lambda.Dockerfile` and pick an IaC (AWS SAM or CDK). Define: one Lambda function, one EventBridge schedule per source set, one HTTP API route, one DynamoDB table, one S3 bucket, one Secrets Manager secret per provider, DLQ, CloudWatch alarms. | Medium — IaC choice affects reproducibility. |
| 8f | GitHub Actions workflow: build & push image to ECR, update Lambda, deploy stack. | Low. |
| 8g | GitHub Actions *fallback* scheduled workflow that runs the same container locally in CI (for users who don't want AWS). | Low. |
| 8h | Smoke test: trigger EventBridge manually in a dev account, observe SyncReport in CloudWatch Logs. | Low. |

### Dependency graph between phases

```
Phase 1 (core refactor)
   ├── Phase 2 (web server)
   ├── Phase 3 (integration framework)
   │       └── Phase 4 (Google Drive provider)
   │               └── Phase 6 (incremental sync)
   ├── Phase 5 (state layer)  ─────────────┐
   │                                       │
   └────────────── Phase 6 (needs 1+4+5) ──┤
                            │              │
                            ├── Phase 7 (exporters rely on StateStore)
                            └── Phase 8 (Lambda deployment; consumes 6+7)
```

Phases 5 and 6 can start once phase 1 is done; they do not require phases 2 or 3 to be finished. Phase 8 is gated on 6 and 7.

---

## 16. Open Questions & Risks

1. **AnkiWeb sync without desktop Anki.** Option C (§13.3.3) is the current plan, but it assumes the user is willing to run a small desktop agent. If not, the only automated path is the unsupported Option B. Need to confirm acceptability before committing to phase 7.
2. **CEDICT cold-start cost on Lambda.** Loading ~120 MB from S3 into `/tmp` and parsing on every cold start may push per-run latency beyond a few seconds. Mitigations: provisioned concurrency (violates scale-to-zero, so avoid), EFS mount (more ops), or splitting CEDICT into a pre-parsed pickle to skip parse time. Needs a quick benchmark before phase 8.
3. **Google Docs → text fidelity.** The current ingest router treats DOCX as the canonical format. Google Docs export-as-DOCX preserves structure but may introduce noise (comments, track changes). Export-as-text drops structure that the chunker might benefit from. Need a small experiment comparing the two for real study notes.
4. **Bedrock determinism.** Temperature is already 0.0, but the LLM can still produce slightly different card sets on re-runs of the same chunk. Chunk-level skipping (§12.4) eliminates the problem in steady state, but first-run results will vary. Acceptable, but worth flagging.
5. **Schema evolution for `CardRecord`.** If we ever add a field (e.g. an HSK level), we need a migration story. Plan: include `schema_version` on every record; on read, transparently upgrade older rows.
6. **Multi-user vs. single-user assumption.** Everything above implicitly assumes one user's deck. If this ever becomes multi-tenant, `StateStore` keys need a `user_id` prefix. Cheap to add now (include `user_id` on every record, default it to a constant) vs. a painful migration later. Recommendation: include it from the start.
7. **Cost ceiling.** EventBridge + Lambda + DynamoDB on-demand + S3 + Secrets Manager for a weekly run should total well under $1/month. Webhook-driven runs scale with edit frequency; worst-case (~hundreds of edits/day) still stays under a few dollars/month. No monitoring dashboards needed at these levels, but CloudWatch billing alarm at $5 is cheap insurance.
8. **"Do we even need DynamoDB?"** For a single-user deck, SQLite on EFS would work. Rejected because EFS attached to Lambda is more moving parts than DynamoDB and has a non-trivial idle cost. Revisit only if Dynamo access patterns become awkward.
