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
17. [Story Breakdown for Implementation](#17-story-breakdown-for-implementation)

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

This subsection specifies, end to end, how we turn "user edits a Google Doc" into "Lambda invokes `run_sync(only_file_ids=[...])`". It is the most intricate piece of the event-driven design, so it gets its own deep dive.

#### 11.3.1 Drive's push-notification model (concepts)

Google Drive does not push *file diffs*. It pushes a bare "something in the scope you registered has changed" ping. Clients are expected to pull the actual diff from the **changes feed**. The moving parts:

| Concept | API | Role |
|---|---|---|
| **Start page token** | `changes.getStartPageToken` | A cursor representing "now". Every subsequent change has a token greater than this one. |
| **Changes feed** | `changes.list(pageToken=...)` | Returns the ordered list of changes since the given token, plus a `newStartPageToken` to store for next time. |
| **Watch channel** | `changes.watch(pageToken=..., address=..., id=..., token=...)` | Registers an HTTPS webhook URL to be notified whenever the changes feed advances. |
| **Channel lifecycle** | implicit | Channels expire (default and max ~7 days). They must be renewed or re-created before expiry; they can also be explicitly stopped via `channels.stop`. |
| **Notification** | HTTPS POST from Google to our endpoint | Carries channel/resource metadata in **headers only**; body is empty. |

Key implication: Drive notifications are **edge-triggered, not level-triggered**. Missing a ping means missing that change window; the `pageToken` is what makes the flow robust against this — as long as the token is persisted, we can always catch up by calling `changes.list` from the last known token, whether or not every webhook actually arrived.

#### 11.3.2 Scope model: per-source-set watches

We deliberately do **not** use a single account-wide watch. Instead, each **source set** (§13.5) that opts into live updates registers its own watch:

- `watchType`: either `"user"` (watches the authenticating user's entire Drive) or `"folder"` (via `files.watch` on a specific folder ID, which narrows the notification surface).
- Drive's `changes.watch` itself is always user-scoped; folder-scoped notifications use `files.watch` and behave differently (they notify on changes to that file or its immediate children, not deeply nested subfolders).
- For the primary use case — "my `Chinese 301` folder" — the recommended pattern is:
  1. A single `changes.watch` on the authenticating account for cheap global coverage.
  2. At processing time, filter the returned changes down to files that live under the configured folder IDs (via `parents` traversal, cached in `StateStore`).
- `files.watch` per folder is listed as an alternative when a source set spans only one narrow folder and we want to minimize notification noise. Both are abstracted behind the same `DriveWatcher` class.

#### 11.3.3 Authentication choice

Three auth shapes are viable; the plan picks the second.

| Option | Works for private personal Gmail Docs? | Works for Google Workspace domain? | Tokens needed at runtime | Verdict |
|---|---|---|---|---|
| **Service account + domain-wide delegation** | No (delegation only works in Workspace) | Yes, but requires admin consent | SA JWT (self-signed) | Rejected for personal Gmail use; kept as an option for Workspace deployments. |
| **OAuth 2.0 user-delegated refresh token, granted once, stored server-side** | Yes | Yes | Long-lived refresh token in Secrets Manager; exchange for access token each cold start | **Selected.** Matches the "one user, one deck" reality. |
| **Service account with explicit file/folder shares** | Yes (docs must be shared with the SA email) | Yes | SA JWT | Backup option when the user doesn't want a user-consented OAuth token on the server. Requires re-sharing every new doc with the SA. |

OAuth setup flow (one-time, human-driven):

1. Operator runs `anki-notes-pipeline auth google-drive` locally.
2. The CLI opens the OAuth consent screen requesting `https://www.googleapis.com/auth/drive.readonly`.
3. The CLI captures the authorization code, exchanges it for a refresh token + access token.
4. The refresh token is written to AWS Secrets Manager under `anki-pipeline/google-drive/<user_id>/refresh_token`.
5. Lambda cold start reads the secret, uses `google-auth`'s `Credentials.from_authorized_user_info` to mint fresh access tokens as needed.

Scopes requested:
- `drive.readonly` — required for `changes.list`, `files.get`, `files.export`.
- `drive.metadata.readonly` is insufficient because we also need to download content.
- We do **not** request `drive.file` (that restricts us to files the app itself created, which defeats the purpose).

#### 11.3.4 Domain, HTTPS, and endpoint verification

Drive enforces several constraints on the webhook target address:

- Must be HTTPS, with a certificate that chains to a publicly trusted root. API Gateway HTTP APIs satisfy this out of the box.
- The domain must be **verified** in [Google Search Console](https://search.google.com/search-console) under the same Google account that issued the OAuth consent. An unverified domain causes `changes.watch` to fail with `push.webhookUrlUnauthorized`.
- Plain `*.execute-api.<region>.amazonaws.com` URLs are **not verifiable**. The plan therefore places API Gateway behind a custom domain we own (e.g. `drive-hook.example.com`), configured with an ACM cert and a Route 53 record pointing at the API Gateway distribution.
- Search Console verification is done once, manually, by adding a DNS TXT record. This is a one-time operator task documented in `infra/README.md`.

Routing:

```
Google Drive ──HTTPS POST──► drive-hook.example.com
                                      │
                                      ▼
                             API Gateway (HTTP API)
                             POST /drive/notifications
                                      │
                                      ▼
                             Lambda: handler_drive_webhook
```

#### 11.3.5 Webhook registration (setup & renewal)

A small "watch manager" Lambda (invoked by CLI command `anki-notes-pipeline drive watch register --source-set ...` for initial setup, and by an EventBridge schedule for renewal) owns channel lifecycle.

Registration call (conceptual):

```python
channel_id   = uuid4().hex
channel_tok  = secrets.token_urlsafe(32)      # shared-secret, verified on each incoming POST
expiration   = int((now + timedelta(days=6)).timestamp() * 1000)  # ms since epoch

body = {
    "id":         channel_id,
    "type":       "web_hook",
    "address":    "https://drive-hook.example.com/drive/notifications",
    "token":      channel_tok,
    "expiration": expiration,
}
resp = drive.changes().watch(pageToken=state.page_token, body=body).execute()

state_store.upsert_drive_channel(DriveChannelRecord(
    channel_id   = channel_id,
    resource_id  = resp["resourceId"],
    token        = channel_tok,
    expiration   = datetime.fromtimestamp(int(resp["expiration"]) / 1000, tz=UTC),
    page_token   = state.page_token,
    source_set   = source_set.name,
))
```

Key points:

- `expiration` is capped at Drive's maximum (~7 days). We use 6 days and run renewal daily so there's always ≥24 h of slack.
- `token` is our own opaque shared secret; Drive echoes it back on every notification as `X-Goog-Channel-Token`. Notifications with a missing or mismatched token are rejected with 401.
- `id` is a per-channel UUID. When a channel ages out or is replaced, the previous `id` is stopped explicitly to avoid double notifications.

Renewal job (`handler_watch_renewal`, scheduled via EventBridge once per day):

1. Query `StateStore` for all `DriveChannelRecord` rows expiring in <48 h.
2. For each, obtain the current `pageToken` from `StateStore` (must be the one used for the last successful `changes.list`).
3. Call `changes.watch` with a fresh `channel_id` and `token`.
4. Persist the new channel record.
5. Call `channels.stop` on the **old** channel to suppress further pings on it.
6. Do these steps in this order so that if step 5 fails, we only have an extra channel temporarily, not a gap.

Explicit teardown (`anki-notes-pipeline drive watch unregister`) calls `channels.stop` and deletes the row.

#### 11.3.6 Synchronization token lifecycle (`pageToken`)

This is the linchpin of correctness.

- Exactly one authoritative `pageToken` per source set lives in `StateStore.DriveChannelRecord.page_token`.
- It is **advanced only after** `run_sync` has successfully persisted all card upserts for every changed file returned in that `changes.list` page.
- On partial failure (e.g. one file's ingest errors), the token is **not** advanced. Next invocation (whether another webhook or the fallback schedule) will re-see the same changes, but §12's content-hash deduplication makes the re-process a no-op for already-handled files.
- Drive's `changes.list` returns a `newStartPageToken` only on the final page. For multi-page diffs, paginate until that field appears, then write the new token atomically.

Atomic advance pattern (DynamoDB):

```
UpdateItem
  Key: {pk: "drive_channel", sk: channel_id}
  ConditionExpression: page_token = :expected_prev_token
  UpdateExpression: SET page_token = :new_token, last_advanced_at = :ts
```

The condition expression protects against two handlers racing to advance past each other (unlikely given Lambda concurrency=1 per channel, but cheap insurance).

#### 11.3.7 The webhook request: shape, verification, response SLA

A Drive notification looks like this:

```
POST /drive/notifications HTTP/1.1
Host:                     drive-hook.example.com
Content-Type:             application/json; charset=UTF-8
Content-Length:           0

X-Goog-Channel-ID:        <our channel_id from watch call>
X-Goog-Channel-Token:     <our shared secret>
X-Goog-Channel-Expiration: Fri, 17 Oct 2025 12:00:00 GMT
X-Goog-Resource-ID:       <opaque resource id from watch response>
X-Goog-Resource-URI:      https://www.googleapis.com/drive/v3/changes?...
X-Goog-Resource-State:    sync | change | remove | update | exists
X-Goog-Message-Number:    42
```

Notes:

- **Body is empty.** API Gateway must be configured not to require a JSON body; a simple `POST` with zero content is valid.
- **`X-Goog-Resource-State` values we handle:**
  - `sync`: the first ping after channel registration. Acknowledged with 200 and otherwise ignored — it carries no diff.
  - `change` / `update` / `exists`: trigger a `changes.list` pull.
  - `remove`: channel was stopped or expired; trigger channel re-registration via the renewal job rather than pulling changes.
- **Response SLA:** Google's docs recommend returning 200 within a couple of seconds and treats anything else (≥300, timeout, connection error) as retry-worthy. We therefore do **not** do the LLM work inside the webhook handler; we only read headers, verify the token, and enqueue (see §11.3.8).

Verification steps inside `handler_drive_webhook`:

1. Read `X-Goog-Channel-ID`. If not in `StateStore`, return 404.
2. Read `X-Goog-Channel-Token`, compare against the stored shared secret using `hmac.compare_digest`. Mismatch → 401.
3. Read `X-Goog-Resource-State`. If `sync` → 200 immediately. If `remove` → enqueue a re-registration task and 200. Otherwise, continue.
4. Check `X-Goog-Message-Number` against the last-seen value stored in `StateStore`. If we've already processed this or a later number for this channel, return 200 without enqueueing (replay / retry absorption).
5. Enqueue a "pull changes" job and return 200.

#### 11.3.8 Two-tier architecture: webhook receiver vs. worker

We split the work across two Lambdas to respect the webhook response SLA and to decouple retries.

```
Drive ──► API Gateway ──► Lambda A: handler_drive_webhook
                              │   (verifies, enqueues, returns 200)
                              ▼
                          SQS queue: drive-change-jobs (FIFO, deduplicated by channel_id)
                              │
                              ▼
                          Lambda B: handler_drive_changes_worker
                              │   (pulls changes, runs run_sync)
                              ▼
                          DynamoDB (StateStore) + exporters
```

Why FIFO + dedup on `channel_id`:

- FIFO with `MessageGroupId = channel_id` guarantees that two pings for the same channel never run in parallel, eliminating `pageToken` races.
- A 5-minute `ContentBasedDeduplication` window absorbs rapid-fire duplicate pings (Drive often sends several for a single edit burst).
- Different source sets (different channels) still run concurrently since they're in different message groups.

Failure routing:

- Worker's `redrive_policy` sends to a DLQ after 3 attempts.
- Webhook handler itself is intentionally near-trivial so it rarely fails; retries from Drive are absorbed by the message-number check.

#### 11.3.9 Processing changes: `changes.list` → file IDs

The worker does the real work:

```python
def handler_drive_changes_worker(event: SQSEvent) -> None:
    for record in event.Records:
        channel_id = record.body["channel_id"]
        channel    = state_store.get_drive_channel(channel_id)
        source_set = source_sets.load(channel.source_set)
        creds      = drive_credentials_for(source_set)

        page_token = channel.page_token
        collected_file_ids: set[str] = set()

        while True:
            resp = drive.changes().list(
                pageToken=page_token,
                spaces="drive",
                includeRemoved=True,
                fields="nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,parents,modifiedTime,md5Checksum,trashed))",
            ).execute()

            for change in resp.get("changes", []):
                if change.get("removed") or change.get("file", {}).get("trashed"):
                    continue
                f = change["file"]
                if not _mime_type_supported(f["mimeType"]):
                    continue
                if not _lives_under_configured_folders(f, source_set, state_store):
                    continue
                collected_file_ids.add(f["id"])

            if "nextPageToken" in resp:
                page_token = resp["nextPageToken"]
                continue
            new_token = resp["newStartPageToken"]
            break

        if collected_file_ids:
            run_sync(
                source_set=source_set,
                settings=settings,
                only_file_ids=sorted(collected_file_ids),
                state_store=state_store,
                exporters=exporters_for(source_set),
            )

        state_store.advance_drive_channel_token(
            channel_id=channel_id,
            expected_prev_token=channel.page_token,
            new_token=new_token,
        )
```

Supported mime types (`_mime_type_supported`):

| mimeType | Handling |
|---|---|
| `application/vnd.google-apps.document` | **Primary case.** Export as DOCX (see §5.3). |
| `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Download via media endpoint. |
| `application/pdf` | Download via media endpoint. |
| `text/markdown`, `text/plain` | Download via media endpoint. |
| `application/vnd.google-apps.folder` | Ignored (folder metadata change, not content). |
| Everything else | Ignored with a debug log. |

Folder filtering (`_lives_under_configured_folders`):

- Walks `parents` via `files.get(..., fields="parents")` recursively, with a per-run memoization cache keyed by `file_id`.
- Long-lived parent→ancestor mappings are cached in `StateStore` under `DriveAncestorCache` records (TTL 7 days) to avoid repeated tree-walks on every webhook.
- Moves into or out of a configured folder are detected here: a doc that *used to* be in scope but has been moved out shows up as a change but fails the ancestor check; we simply skip it (its old `SourceRecord` can be retired in a cleanup pass).

#### 11.3.10 Edge cases

| Situation | Drive signal | Our behavior |
|---|---|---|
| User renames a doc | `change` with `file.name` updated; `fileId` unchanged | Processed like any other update. `SourceRecord.filename` is refreshed. |
| User moves a doc between folders | `change`; `parents` changed | Ancestor cache for that file is invalidated, folder filter re-evaluated. |
| User trashes a doc | `change` with `file.trashed=true` | Skipped in worker; `SourceRecord.retired_at` set. Cards from it remain in inventory unless the user runs a "retire orphan cards" maintenance command (future work). |
| User deletes a doc permanently | `change` with `removed=true` | Same as trashing. |
| User shares a new doc into a watched folder | `change` | Processed as a brand-new source. `SourceRecord` inserted with `first_seen_at`. |
| Permissions revoked mid-run | `files.export` returns 403 | Worker logs, marks that file as errored in `SyncReport`, leaves `pageToken` *un*advanced so retry is possible after re-sharing. |
| Channel silently dies | No notifications arrive | Fallback schedule (§11.5) still calls `changes.list` and catches up from `pageToken`; operator gets a CloudWatch alarm if expected daily pings are missing. |
| `pageToken` becomes invalid (e.g. too old, Drive backend rotation) | `changes.list` returns 404 `Invalid value` | Worker calls `changes.getStartPageToken` to reset, records a warning, and triggers a full re-scan of configured folders via `files.list` as a one-off resync. |

#### 11.3.11 Security

- **Shared-secret token check** (§11.3.7) is the primary defense. Any unauthenticated POST to `/drive/notifications` is rejected with 401.
- **API Gateway rate limiting**: burst limit 20 rps, sustained 5 rps on `/drive/notifications`. Legitimate Drive traffic is well under this.
- **WAF rule**: reject POSTs whose `Host` header isn't our custom domain, which defends against cert-less direct hits on the `*.execute-api` URL.
- **No secrets in the notification**. Channel IDs and resource IDs are opaque and non-sensitive; we still avoid logging the `token` header.
- **TLS**: API Gateway enforces TLS 1.2+ on the custom domain.
- **OAuth refresh token handling**: stored in Secrets Manager with KMS encryption; Lambda execution role has read permission only on its own user's secret path.

#### 11.3.12 Quotas, cost, and sizing

- Drive API default quota: 1,000 requests / 100 s / user, 10,000 / 100 s / project. `changes.list` is one request per page (usually one page per ping). At personal-use volumes this is nowhere near the limit.
- Each `changes.watch` renewal is one API call; once/day is negligible.
- Expected steady-state cost per month:
  - API Gateway HTTP API: ~100 webhook hits × $1.00 / million requests ≈ $0.
  - Lambda A (webhook): a few ms per invocation, essentially free tier.
  - Lambda B (worker): dominated by LLM calls, same as the scheduled case.
  - SQS FIFO: first 1M requests free; we'll use ~hundreds/month.
  - Custom domain + ACM cert: free; only cost is Route 53 hosted zone ($0.50/mo).

#### 11.3.13 Observability

Every webhook invocation emits a single structured log line (JSON):

```json
{
  "event":            "drive.webhook.received",
  "channel_id":       "…",
  "resource_state":   "change",
  "message_number":   42,
  "source_set":       "weekly-chinese-notes",
  "verified":         true,
  "enqueued":         true,
  "duration_ms":      18
}
```

Worker emits:

```json
{
  "event":               "drive.changes.processed",
  "channel_id":          "…",
  "source_set":          "weekly-chinese-notes",
  "changes_seen":        7,
  "files_in_scope":      2,
  "files_run":           2,
  "page_token_advanced": true,
  "duration_ms":         4120
}
```

CloudWatch alarms:

- DLQ depth > 0 for > 5 min → notify.
- No `drive.webhook.received` for > 36 h on any channel marked `expected_traffic=daily` → notify (channel may be silently dead).
- `changes.list` 4xx rate > 10% → notify (likely auth or token invalidation).

#### 11.3.14 Local development & testing

- **Unit tests**: verify header parsing, token constant-time compare, message-number dedupe, mime-type filter, folder ancestor filter. All pure Python, no Drive client needed.
- **Worker tests**: mock `googleapiclient.discovery` with [`responses`](https://github.com/getsentry/responses) or hand-rolled fakes; assert that `pageToken` advances only on success.
- **End-to-end local loop**: `anki-notes-pipeline drive webhook simulate --channel-id <id> --state change` crafts a valid POST to the local FastAPI app, exercising the same handler code path as production.
- **Real-Drive loop** (operator-run, not CI): an `ngrok` tunnel + a sacrificial Google account lets us exercise the live `changes.watch`/webhook path end to end. Search Console verification for the ngrok hostname is skipped by using a long-lived dev-domain subdomain that's already verified.

#### 11.3.15 Data model addition

`DriveChannelRecord` (added to §12.1):

| Field | Type | Purpose |
|---|---|---|
| `channel_id` | str (PK) | UUID we generated for `changes.watch`. |
| `resource_id` | str | Returned by Drive; required to stop the channel. |
| `token` | str (encrypted) | Shared secret echoed back in `X-Goog-Channel-Token`. |
| `expiration` | datetime | When Drive will stop sending pings. |
| `page_token` | str | Current `changes.list` cursor. |
| `last_message_number` | int | For replay detection. |
| `last_advanced_at` | datetime | For observability. |
| `source_set` | str | Which source-set config owns this channel. |
| `created_at` | datetime | For audits. |
| `expected_traffic` | enum("daily", "weekly", "sparse") | Drives the "silent channel" alarm. |

### 11.4 Idempotency and retries

- API Gateway → Lambda can retry on 5xx. Handlers must be safe to run twice on the same input. §12 (persistent memory) ensures this at the data layer; the handler layer enforces it by making all writes keyed on content hashes and Drive revision IDs.
- A Lambda **dead-letter SQS queue** captures terminal failures for manual replay. Alarms on DLQ depth are the "something broke" signal.

### 11.5 Scheduling for local/GitHub Actions fallback

- Local: `cron` invokes `anki-notes-pipeline schedule --source-set weekly-chinese-notes` which is the exact function the Lambda calls.
- GitHub Actions: a `.github/workflows/weekly-sync.yml` on `schedule:` runs the same command inside the container image used for Lambda, so behavior is identical. Secrets come from repo secrets instead of AWS Secrets Manager; the settings loader abstracts this.

### 11.6 Waiting for the end of an editing session before processing

A naive "process on every edit" trigger is wasteful and, for Google Docs specifically, misleading: Drive emits push notifications frequently during a live editing session (multiple per minute is normal), and `changes.list` will happily return the same `fileId` dozens of times before the user is done. What we really want as the **start condition** is "the user has stopped editing this doc for long enough that further edits are unlikely in the immediate term."

This subsection specifies how we add that start condition, what it does to change-tracking logic, and how it affects resource usage.

#### 11.6.1 Defining "session ended"

Drive does not expose an explicit "editing session ended" signal. We infer it from the absence of further change notifications. Two definitions worth considering:

| Definition | Description | Trade-off |
|---|---|---|
| **D1 — Quiescence window (chosen)** | A doc is considered "settled" once no `change` notification has arrived for it in the last *N* minutes. | Simple, provider-agnostic, tunable per source set. |
| D2 — Explicit user signal | The user taps a "done" button in a companion UI, or adds a specific tag/label (e.g. renames the doc with a `✓` suffix, or moves it to a "ready" subfolder). | More accurate, but requires ritualized behavior from the user and a UI. Useful as an override, not as the default. |
| D3 — Edit-rate heuristic | Start processing when the instantaneous change-notification rate drops below some threshold. | Sensitive to tuning, harder to reason about, no obvious benefit over D1. Rejected. |

The plan uses **D1 as the primary mechanism**, with **D2 available as an override** (a "force now" API + a "mark done" Drive label) for users who want to short-circuit the wait.

Per-source-set configuration:

```yaml
source_sets:
  weekly-chinese-notes:
    # ... existing fields ...
    edit_settling:
      enabled: true
      quiet_minutes: 10          # N
      max_delay_minutes: 120     # hard ceiling (see §11.6.4)
      allow_force_override: true
```

Typical default: `quiet_minutes = 10`. The value is deliberately generous because (a) the cost of waiting an extra few minutes is negligible, and (b) the cost of processing mid-session is re-running the LLM on a chunk that will change again.

#### 11.6.2 Mechanism: debounced per-file timers

Implemented as a **per-file debounce** on top of the existing two-tier webhook architecture from §11.3.8. Conceptually:

```
Drive ──► API Gateway ──► Lambda A (webhook)
                              │  verifies, identifies affected fileIds
                              ▼
                          SQS FIFO (drive-change-events)  ── one message per (channel_id, fileId)
                              │
                              ▼
                          Lambda C (debouncer)
                              │  upserts a "pending" record per fileId with a scheduled
                              │  `ready_at = now + quiet_minutes`, or extends an existing one
                              ▼
                          DynamoDB table: PendingEdits  (TTL-driven)
                              │
                              ▼
                          EventBridge rule (every 1 minute)
                              │  scans PendingEdits WHERE ready_at <= now
                              ▼
                          Lambda B (worker)
                              │  processes the settled fileIds via run_sync
                              ▼
                          StateStore advances `pageToken`, records cards, exports
```

Two important changes versus §11.3.8:

1. The SQS FIFO queue no longer drives the worker directly. It drives a **debouncer** whose only job is to maintain the `PendingEdits` table. This is cheap, fast, and idempotent.
2. A new **polling Lambda (B)** runs on a 1-minute EventBridge rule (or is invoked by a DynamoDB Streams→Lambda flow keyed on TTL expiry if we want sub-minute precision — see §11.6.6).

`PendingEdits` schema:

| Field | Type | Purpose |
|---|---|---|
| `pk` | `"pending#<source_set>"` | Partition key; groups by source set. |
| `sk` | `fileId` | Sort key; one row per file currently "cooling off". |
| `first_seen_at` | ISO datetime | When the first notification in the current session arrived. |
| `last_seen_at` | ISO datetime | When the most recent notification arrived. |
| `ready_at` | ISO datetime | `last_seen_at + quiet_minutes`. |
| `hard_deadline_at` | ISO datetime | `first_seen_at + max_delay_minutes` (§11.6.4). |
| `message_count` | int | Number of notifications coalesced into this row (observability). |
| `last_message_number` | int | For replay absorption at the debouncer tier. |
| `force` | bool | Set by the override API to bypass `ready_at`. |

Debouncer logic is a conditional upsert:

```
UpdateItem PendingEdits
  Key: (pk="pending#<source_set>", sk=fileId)
  UpdateExpression:
    SET last_seen_at = :now,
        ready_at     = :now_plus_N,
        message_count = if_not_exists(message_count, 0) + 1,
        first_seen_at = if_not_exists(first_seen_at, :now),
        hard_deadline_at = if_not_exists(hard_deadline_at, :now_plus_max)
  ConditionExpression: attribute_not_exists(force) OR force = :false
```

That single atomic update is all the debouncer needs to do per incoming event.

#### 11.6.3 Interaction with the `pageToken`

This is where change tracking gets subtle. In §11.3.6 the rule was: advance `pageToken` only after every change on a `changes.list` page has been *persisted* by `run_sync`. With debouncing, we have an in-between state: a change has been *observed* (so we don't want to re-pull it from Drive) but not yet *processed* (so we can't let downstream artifacts consider it done).

The plan splits responsibility cleanly:

- The **debouncer** (Lambda C) is allowed to advance `pageToken` as soon as it has durably recorded the affected fileIds in `PendingEdits`. This is safe because `PendingEdits` itself is durable; losing the `pageToken` cursor after that point would not lose information, it would only cause one redundant (idempotent) re-pull.
- The **worker** (Lambda B) never touches `pageToken`. It only reads `PendingEdits` and calls `run_sync`.

Concretely, `StateStore.DriveChannelRecord.page_token` now represents "Drive changes I have *observed*, whether or not they are yet processed." A new per-source-set view, `PendingEdits`, represents "observed but not yet processed."

Invariant (useful for reasoning and testing):

> *For every fileId in `PendingEdits`, there exists a `SourceRecord` whose `revision_id` may be stale relative to Drive but whose on-disk cards are consistent with some past revision. For every fileId NOT in `PendingEdits`, `SourceRecord.revision_id` reflects the content that was last successfully processed.*

#### 11.6.4 Bounded delay (preventing indefinite postponement)

Pure debouncing has a failure mode: a user who keeps typing sporadically every 9 minutes would never trigger processing with `quiet_minutes=10`. Two guards:

1. **Hard deadline (`max_delay_minutes`)**. `PendingEdits.hard_deadline_at` is set once on the first notification of a session and never extended. The worker treats a row as ready when `now >= min(ready_at, hard_deadline_at)`. Default: 2 hours. This guarantees progress even under pathological edit patterns.
2. **Session reset on long gaps**. If a notification arrives for a fileId whose row has already been consumed (no pending row exists), it starts a fresh session with a new `first_seen_at`. This is the common case — most users edit a doc, stop for hours, then come back later — and it must feel natural, not like edits are being "queued forever."

The two knobs together express a clean intent:

- *Short bursts* (minutes): debounced; processed exactly once at the end.
- *Long sessions* (hours): processed at least every `max_delay_minutes`, even while still active.
- *Discontinuous edits separated by > `quiet_minutes`*: processed as separate sessions.

#### 11.6.5 Interaction with `run_sync` and per-chunk change tracking

The content-level change tracking from §12.4 is what makes the debounce safe and efficient when a session is *cut short* by the hard deadline. Suppose a user is in the middle of a marathon edit and we force-process after 2 hours:

1. The worker pulls the current Drive revision, downloads it, hashes it.
2. If the hash matches the last-processed hash, nothing to do (the user may have edited and reverted).
3. Otherwise, re-chunk, hash each chunk. Only chunks with new hashes go through the LLM.
4. Cards are upserted by natural key; unchanged ones are no-ops.
5. `SourceRecord.revision_id` is advanced to the just-processed Drive revision.
6. The `PendingEdits` row is **deleted only if** `last_seen_at ≤ run_started_at`. If new notifications arrived while the run was in flight, the row is left behind with its original `first_seen_at` preserved but a fresh `ready_at`, ensuring another round will fire later.

This design means "force-processing" in the middle of a session is never destructive — it's just an early incremental pass that the next debounced pass will correct if needed.

#### 11.6.6 Worker triggering: polling vs. scheduled vs. DynamoDB TTL

Three mechanisms are viable for turning "this row just became ready" into a Lambda invocation:

| Mechanism | Precision | Idle cost | Complexity | Verdict |
|---|---|---|---|---|
| **EventBridge rule every 1 min → Lambda scans `PendingEdits` for ready rows** | ~60s resolution | Negligible; a query with `ready_at <= now` on a small table is effectively free. | Low. | **Default.** Matches the "no always-on" rule and is trivial to reason about. |
| DynamoDB Streams + TTL on `ready_at` | Seconds | Negligible. | Medium — TTL-based delivery is eventually consistent, with up to ~48h slack on the SLA. Drive edits don't need sub-minute response, so the extra complexity isn't worth it. | Not recommended. |
| Step Functions wait state per file | Sub-second | Per-file cost; many active sessions × many hours → non-trivial. | Higher; also harder to cancel/extend a wait. | Rejected for this use case. |

Polling is selected as the default. A 1-minute granularity is well inside the noise for study-notes workloads.

#### 11.6.7 Observability changes

Two new log events and two new metrics:

- `drive.debounce.extended` — every time an existing `PendingEdits` row has its `ready_at` pushed out. Field: `message_count` after the update. Lets us see how "chatty" sessions are in practice.
- `drive.debounce.fired` — every time a row transitions to processing. Fields: `waited_seconds = ready_at - first_seen_at`, `message_count`, `reason ∈ {quiet, hard_deadline, force}`.
- Metric: **p50 / p95 wait-to-process latency**, per source set. Used to tune `quiet_minutes`.
- Metric: **sessions collapsed per processing run** = `message_count` distribution. Should trend > 1 for this feature to be earning its keep.

Alarms:

- `drive.debounce.fired reason=hard_deadline` fires more than, say, 5× in a rolling 24h window → notify, because that's a signal the `quiet_minutes` is too short for this user's editing style.

#### 11.6.8 Effects on resource usage

The feature is a net reducer of resource usage in the steady state, because its entire purpose is to collapse *k* notifications into 1 run. A concrete back-of-envelope:

Assume one editing session produces ~30 `change` notifications over 15 minutes (a realistic number for an active Google Docs edit). Without debouncing, each notification that passes the mime-type / ancestor filter produces one `changes.list` + one `run_sync`. With debouncing:

| Resource | No debounce | With debounce | Change |
|---|---|---|---|
| Lambda A (webhook) invocations per session | 30 | 30 | unchanged — still responds to every ping |
| Lambda C (debouncer) invocations | 0 | 30 | +30 trivial writes (≈1 ms each) |
| Lambda B (worker) invocations | up to 30 | 1 | **−29** (each one is the expensive Bedrock-using one) |
| `changes.list` API calls | 30 | 1 | **−29** |
| `files.get` / `files.export` | 30 | 1 | **−29** |
| Bedrock token spend | 30× new-chunks work | 1× | **≈ −96%** for this session |
| DynamoDB writes | ~30× `SourceRecord` upserts + chunk/card upserts | 1× all that, plus 30 `PendingEdits` upserts | Roughly flat; slightly more small writes, far fewer large transactions |
| API Gateway requests | 30 | 30 | unchanged |
| SQS messages | 30 | 30 (into debouncer) | unchanged in count; the expensive worker stage is what shrinks |

The dominant cost in this system is Bedrock. Cutting worker invocations by an order of magnitude per session roughly shrinks the LLM bill by the same factor in the webhook-driven path. The added debouncer Lambda and `PendingEdits` table add costs measured in cents/month, dwarfed by the savings.

Corner cases where resource usage *grows*:

1. **One-shot edits.** A user opens a doc, makes a single change, closes it. Debouncing adds `quiet_minutes` of latency but otherwise costs the same: 1 debouncer write, 1 worker invocation.
2. **Many small docs edited together.** e.g. mass-applying a tag. `PendingEdits` might accumulate hundreds of rows simultaneously. Acceptable: reads are by `(pk, ready_at)` and DynamoDB on-demand handles bursty write volume natively. The worker processes all ready rows in a single fan-out batch (one `run_sync` per fileId or grouped into one `run_sync` with many `only_file_ids` — the orchestrator already accepts a list).
3. **Abandoned sessions.** A user starts editing then walks away for days. With `max_delay_minutes = 120`, we still fire once at 2h and then once more at the end if they return. Acceptable. To be thorough, `PendingEdits` rows carry a DynamoDB TTL of `hard_deadline_at + 30 days` as a garbage-collection backstop.

#### 11.6.9 Effects on change-tracking invariants

Re-stating the §12.4 layered change detection explicitly now that events are debounced:

1. **Document level.** Debouncer coalesces many notifications for one `fileId` into one "the file is probably different from last time we processed it." The worker still verifies against `SourceRecord.revision_id` / content hash before doing any work, so spurious debounce fires (e.g. user typed a character and then `⌘Z`'d it) cost only one Drive metadata fetch.
2. **Content level.** Unchanged if the revision differs but content bytes hash the same (happens with some Google Docs metadata-only edits like share-settings changes). We short-circuit with no LLM.
3. **Chunk level.** When the document did change, the re-chunk + chunk-hash layer picks up only the edited chunks. The debounce window means a session that edits five chunks over 20 minutes results in one worker run that re-LLMs five chunks — not 30 worker runs re-LLMing the same five chunks over and over.
4. **Card level.** Cards upsert by natural key; unchanged cards → no-op → no AnkiWeb sync churn. This matters: without debouncing, an in-progress session can produce transient card states (e.g. a half-typed sentence that the LLM misreads) that would all get pushed to AnkiWeb and then overwritten. With debouncing, the user's draft never leaves our system.

The integrity guarantees from §12 are preserved; debouncing only changes *when* the worker runs, not *how* it reasons about state.

#### 11.6.10 Overrides and escape hatches

1. **Force-process a specific file.** `POST /api/integrations/google-drive/force-process` with `{"file_id": "..."}` sets `PendingEdits.force = true` for the matching row and adjusts `ready_at = now`. Worker picks it up on the next poll.
2. **Force-process a whole source set.** `POST /api/sync/run` with `{"source_set": "..."}` bypasses `PendingEdits` entirely (goes through the manual-trigger T3 path) and then clears any rows for files covered by the run.
3. **"Mark done" label.** Optional: if the user renames a watched doc to end with a configurable sentinel (e.g. `[done]`), the webhook handler treats that revision as settled immediately. Cheap to implement (regex on `file.name` in the webhook verifier) and matches D2 from §11.6.1.
4. **Pause debouncing entirely.** `edit_settling.enabled: false` reverts to the §11.3 behavior.

#### 11.6.11 Provider-agnosticism

The debouncing layer is defined in terms of `(provider, external_id)`, not Drive specifics. When future providers (Notion, Dropbox, OneNote) emit edit events, they publish to the same `PendingEdits` table with their own `provider` column. Each provider's webhook handler stays small; the debouncer and worker are shared infrastructure. This is why §14's module layout puts `sync/debounce.py` in the generic `sync/` package rather than inside `integrations/google_drive/`.

#### 11.6.12 Worked example: 1-hour lesson with intermittent note-taking

Your reported pattern:

- A document is held open for roughly 1 hour (the duration of a lesson).
- Within that hour, there are typically **3 note-taking bursts**.
- Between bursts there is a **10–15 minute quiet window**.
- After the third burst the lesson ends and the doc goes quiet for the rest of the day.

Reasonable timing assumptions for the worked example below:

| Interval | Duration |
|---|---|
| Burst 1 (active typing) | ~10 min, ~20 notifications |
| Gap 1 (listening) | 10–15 min, 0 notifications |
| Burst 2 | ~10 min, ~20 notifications |
| Gap 2 (listening) | 10–15 min, 0 notifications |
| Burst 3 | ~10 min, ~20 notifications |
| Post-lesson | silent until next lesson (hours) |
| **Total elapsed** | **~50–60 min, ~60 notifications** |

The tuning question is: does `quiet_minutes` comfortably exceed 15 min, or not?

##### Tuning comparison

| Setting | Worker runs per lesson | Latency: cards visible after final edit | Fragility (if a gap happens to be 16 min) |
|---|---|---|---|
| `quiet_minutes = 5` (aggressive) | 3 (one per burst) | 5 min | Ultra-stable; but triples LLM spend vs. merged runs |
| `quiet_minutes = 10` (generic default) | Usually 3; sometimes 1–2 | 10 min after each burst | **Brittle** — a 10-min gap is right at the threshold; a 10½-min gap splits the lesson |
| `quiet_minutes = 20` (**recommended**) | **1 per lesson** | 20 min | Safe margin above the observed 15-min max gap |
| `quiet_minutes = 25` (conservative) | 1 per lesson, occasionally 1 across two back-to-back lessons | 25 min | Safe, slightly worse immediacy |
| `quiet_minutes = 45` (very conservative) | 1 per lesson; merges some back-to-back lessons | 45 min | Over-merges; obscures per-lesson audit story |

**Recommendation for this user: `quiet_minutes = 20`.** Rationale:

- 20 min ≥ max observed gap (15 min) + 5 min of slack — covers the worst gap plus instrumentation/timer skew.
- Below the plausible *post-lesson* silence (which is hours), so sessions still close cleanly after the lesson ends.
- Produces exactly one worker run per lesson, which is the natural "audit unit" for a student's notes.

##### `max_delay_minutes` for this pattern

With lessons capped at ~60 min, the 120-min default is overkill. Tightening it lets the hard deadline act as a safety net for the edge case where a lesson overruns *or* where a subsequent study session blends into the lesson without a 20-min break.

**Recommendation: `max_delay_minutes = 90`.**

- A normal lesson (~60 min) never trips the deadline — the session closes via `quiet_minutes` first.
- If editing continues >90 min for any reason, the worker fires anyway, ensuring progress.
- Leaves a 30-min cushion over the expected 60-min lesson length, so instructor overruns don't trigger the hard deadline unnecessarily.

##### Configuration block

```yaml
source_sets:
  lesson-notes:
    # ... Google Drive folder(s) for lesson docs ...
    edit_settling:
      enabled: true
      quiet_minutes: 20
      max_delay_minutes: 90
      allow_force_override: true
```

##### Resource-usage math for this pattern

Per lesson (60 min, 3 bursts, ~60 notifications):

| Resource | No debounce | `quiet_minutes=10` (brittle) | `quiet_minutes=20` (recommended) |
|---|---|---|---|
| Webhook-Lambda invocations | 60 | 60 | 60 |
| Debouncer-Lambda invocations | 0 | 60 | 60 |
| Worker-Lambda invocations | up to 60 | ~3 (usually splits) | **1** |
| `changes.list` API calls | 60 | ~3 | **1** |
| `files.export` (Google Doc → DOCX) | up to 60 | ~3 | **1** |
| Bedrock-driven chunk re-processing | up to 60 (duplicated across splits) | ~3 (partial dedup by chunk hash) | **1** (full chunk-level dedup) |
| AnkiWeb pushes (at the end of each run) | up to 60 | ~3 | **1** |

Cross-week (assume ~3 lessons/week):

| Metric | No debounce | `quiet_minutes=10` | `quiet_minutes=20` |
|---|---|---|---|
| Worker runs/week | ~180 | ~9 | **~3** |
| Bedrock tokens/week | ~180 × per-chunk × duplication | ~9 × per-chunk | **~3 × per-chunk**, with §12.4 chunk hashing making later runs nearly free for unchanged chunks |
| Expected Bedrock savings vs. no debounce | baseline | ~95% | **~98%** |

The jump from `quiet_minutes=10` to `quiet_minutes=20` is disproportionately valuable for this specific pattern because 10 sits *right at* the gap boundary — every lesson is a coin flip on how many runs fire. 20 moves you off the boundary and into a stable regime.

##### Immediacy considerations

Trade-off: at `quiet_minutes=20`, new cards don't appear until ~20 min after the final edit of a lesson. For a student reviewing notes that evening, this is negligible. If immediacy ever matters (e.g. wanting cards right after class to quiz each other on the bus ride home), there are two cheap escape hatches from §11.6.10 that don't require re-tuning:

1. **Force override endpoint**: the FastAPI `POST /api/integrations/google-drive/force-process` with the doc's `fileId` — fires the worker on the next 1-min tick.
2. **"[done]" sentinel in filename**: renaming the doc to end with `[done]` short-circuits the quiet window; the webhook handler marks the `PendingEdits` row as `force=true` immediately.

The hard deadline (`max_delay_minutes`) is not a useful immediacy tool here — it's strictly a safety net for pathological patterns, not a normal-operation lever.

##### Effect on change-tracking invariants

The §12.4 four-layer change detection works *better* under this tuning than under the generic default:

- **Document level**: one `SourceRecord.revision_id` bump per lesson instead of three.
- **Content level**: no effect — cheap hash comparison regardless.
- **Chunk level**: the single worker run sees the *final* state of the document, so `chunk_sha256` comparisons identify exactly the set of chunks whose contents differ from last week's lesson notes. With `quiet_minutes=10` + split sessions, the middle-of-lesson runs would compute chunk hashes against an intermediate state, then the next run would see more chunks "new" than is really meaningful. The tighter the debounce, the more jittery the chunk-level story becomes.
- **Card level**: AnkiWeb sees one clean upsert batch per lesson, not a mid-lesson push of "lesson so far" followed by corrections. This improves the diff readability in the AnkiWeb audit feed and eliminates any case where an intermediate card state gets surfaced to the user and then overwritten minutes later.

##### Summary of the tuning choice

| Knob | Generic default | **This user's lessons** | Why the change |
|---|---|---|---|
| `quiet_minutes` | 10 | **20** | Observed inter-burst gaps reach 15 min; need safe margin above that |
| `max_delay_minutes` | 120 | **90** | Lessons are ~60 min; 90 is a snug safety net |
| `allow_force_override` | true | true | Unchanged; "[done]" sentinel + force endpoint cover immediacy needs |

`quiet_minutes = 20` is the only material change vs. the default; `max_delay_minutes` is a minor tightening with no visible behavior change in the normal case.

#### 11.6.13 Summary of application-logic deltas

| Area | Before | After |
|---|---|---|
| Webhook handler | Verifies + enqueues for worker. | Verifies + enqueues for **debouncer** (same shape, different consumer). |
| Worker trigger | SQS message per notification. | EventBridge 1-minute tick reading `PendingEdits`. |
| `pageToken` ownership | Worker advances it after successful `run_sync`. | **Debouncer** advances it after successful `PendingEdits` write. |
| StateStore | `DriveChannelRecord`, `SourceRecord`, `ChunkRecord`, `CardRecord`. | Adds `PendingEdits`. `DriveChannelRecord` gains `edit_settling` snapshot for audit. |
| run_sync | Called per notification. | Called once per "settled" session, with `only_file_ids` collapsed from many events. |
| Invariant | "pageToken reflects what's processed." | "pageToken reflects what's observed; PendingEdits reflects the gap." |
| Test surface | Worker tests. | Worker tests + debouncer tests (pure DynamoDB-layer logic, very easy to mock). |

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

#### 13.3.1 The AnkiWeb API landscape (what actually exists)

This is worth stating plainly up-front, because it shapes every design decision that follows:

- **AnkiWeb has no public API for uploading cards/notes from third-party apps.** The AnkiWeb team has explicitly stated that "because other clients can cause problems, AnkiWeb does not currently allow access from browser extensions or other third-party clients." There is no OAuth flow, no REST endpoint for notes, no published schema.
- The **sync protocol** between desktop/mobile Anki and AnkiWeb is documented in the Anki source tree (`rslib/src/sync/`), but it's a bidirectional full-collection sync built for Anki itself. It is not a "push a note" API; it's closer to "merge two whole databases, including scheduling state." Re-implementing it from scratch for our exporter would mean owning a mirror of Anki's collection format, including media, note types, scheduling, and historical review logs. Not viable.
- What *is* official and stable is the **AnkiConnect add-on**: a local HTTP JSON-RPC server that exposes Anki's internal API to other processes on the same machine (default bind `127.0.0.1:8765`). AnkiConnect has full actions for note creation, update, lookup, deletion, media import, deck and model management, and a passthrough `sync` action that tells desktop Anki to sync to AnkiWeb. This is the canonical way for third-party tools to land cards on AnkiWeb.
- AnkiWeb itself *does* have a web UI with forms (login, deck edit, etc.). Scripting it via session cookies is possible but undocumented, unsupported, and explicitly discouraged by the AnkiWeb operators. We list this only as a last-resort fallback.

Implication for the plan: the exporter is **always** really an AnkiConnect client. The three "options" below are different answers to "how does our cloud-side pipeline talk to an AnkiConnect instance that lives on the user's desktop?"

#### 13.3.2 Option A — Direct AnkiConnect (local/self-hosted deployments only)

Used when the pipeline runs on the same machine as Anki (e.g. a user running `anki-notes-pipeline run …` locally, or a server the user hosts on their LAN).

- Exporter POSTs JSON-RPC to `http://127.0.0.1:8765`.
- Works unchanged in all three CLI / FastAPI / AnkiConnect server modes as long as the host has network access to the add-on.
- Does not work for Lambda — Lambda cannot route to a desktop in the user's home network.

#### 13.3.3 Option B — AnkiWeb session-cookie client (last-resort fallback)

- Log into AnkiWeb with stored credentials, maintain a session cookie, drive the web UI's undocumented endpoints (primarily the CSV import form).
- Brittle: any HTML change breaks it. Also likely violates AnkiWeb's stated stance on third-party access; we don't enable this by default.
- Kept documented only so a user who truly can't run desktop Anki has an escape hatch that still produces a deck on AnkiWeb.

#### 13.3.4 Option C — Pull-based desktop agent (**selected for Lambda mode**)

Because the AWS-hosted exporter can't initiate connections to a home LAN, we invert the relationship: our Lambda exposes a small HTTPS API that holds a delta feed; a tiny agent on the user's desktop polls it, applies changes via AnkiConnect, and acks back.

This is fully described in §13.3.7; it is the recommended default for any non-local deployment.

#### 13.3.5 AnkiConnect: the operational details that matter

These are the parts of the AnkiConnect spec our exporter depends on. Versions and field names below reflect AnkiConnect's public action reference (latest stable version at planning time is `v23.10.29.0`, API version `6`).

##### Request envelope

Every request is an HTTP POST to the AnkiConnect base URL (default `http://127.0.0.1:8765`) with a JSON body:

```json
{
  "action":  "<action name>",
  "version": 6,
  "params":  { ... action-specific ... },
  "key":     "<optional API key if configured>"
}
```

Every response is:

```json
{ "result": <action-specific or null>, "error": <string|null> }
```

We always send `version: 6`. Error handling policy: treat `error != null` as an actionable failure — never silently skip.

##### Authentication

AnkiConnect supports an optional shared-secret `apiKey`. We treat this as required for any AnkiConnect instance that listens on anything other than `127.0.0.1`. The key is stored in the same secrets backend as other provider secrets (AWS Secrets Manager in Lambda mode, `.env` / keychain locally). We verify at exporter startup by calling `requestPermission`; if it returns `{permission: "granted", requireApiKey: true}` we know we're configured correctly.

##### Actions the exporter uses

| Action | Purpose | Notes |
|---|---|---|
| `version` | Handshake, feature gate. | Fail fast if `< 6`. |
| `requestPermission` | Confirm AnkiConnect will honor our requests and whether `apiKey` is required. | Called once on agent startup. |
| `deckNames` | Confirm target deck exists. | If missing, we call `createDeck` (configurable). |
| `createDeck` | Auto-create the target deck. | Used only when `auto_create_deck: true` in the source-set config. |
| `modelNames`, `modelFieldNames` | Confirm target note type and that its fields match our exporter's mapping. | Validation is done once per agent run; a mismatch fails loudly with a clear message rather than silently producing broken notes. |
| `createModel` | Optional: create a "Chinese vocabulary" note type if the user doesn't already have one. Guarded behind `auto_create_note_type: true`. | Fields we ship: `Simplified`, `Traditional`, `Pinyin`, `Meaning`, `PartOfSpeech`, `UsageNotes`, `SourceRef`, `ExtId`. `ExtId` is our canonical per-card identifier (see §13.3.6); it ends up both in a field and in a tag for reliable lookup. |
| `canAddNotesWithErrorDetail` | Batch-check whether a list of notes can be added without creating duplicates. Returned payload includes a per-note `canAdd` + `error` so we know which specific notes collide. | Preferred over the older `canAddNotes` because it surfaces the reason per note. |
| `addNote` / `addNotes` | Create notes. `addNotes` takes an array and returns an array of note IDs (or `null` for ones it couldn't create — we inspect `canAddNotesWithErrorDetail` beforehand to avoid relying on silent nulls). | Batching: we use `addNotes` with a batch size of 50. Larger batches work, but 50 keeps error messages scoped. |
| `updateNoteFields` / `updateNote` | Modify an existing note's fields (and tags, with `updateNote`). | `updateNote` is newer (2023+); exporter prefers it when available and falls back to `updateNoteFields` + `updateNoteTags` otherwise. |
| `findNotes` | Look up existing note ID by our tag `ext_id:<uuid>`. This is the bridge between our `CardRecord.card_id` and Anki's `note_id`. | Query used: `tag:"ext_id:<card_id>"` — safer than relying on the `Simplified` field being a unique key. |
| `notesInfo` | Read back current fields for conflict detection. | Used only when the exporter detects a "local update that may conflict with a user edit" case (§13.3.6). |
| `addTags` / `removeTags` / `updateNoteTags` | Tag management (see tagging policy §13.3.6). | |
| `storeMediaFile` | Upload media (currently unused; vocabulary cards have no images) but kept in the roadmap for future card types. | |
| `sync` | Ask desktop Anki to push to AnkiWeb. | Called once at end of an exporter run. Best-effort: a failure here leaves local Anki in a valid state; next sync picks up the delta. |
| `multi` | Batch multiple unrelated actions into one HTTP round trip. | Used to reduce latency on large exports. |

##### Actions the exporter intentionally does **not** use

- Any scheduling / review-state mutation (`setDueDate`, `forgetCards`, `answerCards`, `setEaseFactors`, etc.). The exporter never touches the user's review progress.
- `deleteNotes`. Cards that disappear from source documents are marked `retired_at` in `StateStore` but left alone on AnkiWeb. Deleting a note there would destroy review history; that's a manual decision the user makes from Anki's UI.

#### 13.3.6 Identity, duplicates, and conflict resolution

Our `CardRecord.card_id` is a stable UUID minted the first time a card is seen. Anki has its own `noteId` (timestamp-based, assigned by Anki). We need a bidirectional mapping that survives across runs and across user edits in Anki's UI.

Design:

1. **Each exported note carries a tag `ext_id:<card_id>`.** Set at creation time, never edited. This tag is our source of truth for "is this note ours?" — far more reliable than first-field matching, because the user may edit the `Simplified` field, merge notes, or use a note type with a different field order.
2. **Each exported note also stores `<card_id>` in a hidden `ExtId` field** on the "Chinese vocabulary" note type. The tag alone would be enough, but duplicating into a field makes it visible in the Anki browser and in CSV exports, which helps debugging.
3. `StateStore.CardRecord.ankiweb_note_id` caches the `noteId` returned by AnkiConnect after a successful create. On subsequent runs we try this ID first via `notesInfo`; if the note still exists and still has the expected `ext_id:<card_id>` tag, we use it directly. If not (user deleted or un-tagged), we fall back to `findNotes` keyed on the tag.
4. If `findNotes` returns nothing, we treat the card as "new on AnkiWeb even though it's old in our state" and call `addNote`, then update our `ankiweb_note_id`.
5. If `findNotes` returns multiple notes (user accidentally duplicated), we log a warning, pick the earliest `noteId`, update that one, and leave the others alone. The exporter never merges or deletes on the user's behalf.

##### Duplicate-first-field case

AnkiConnect's default behavior rejects `addNote` when the first field matches an existing note in the same note type. Our exporter handles this by always calling `canAddNotesWithErrorDetail` **first** with the full batch; any note marked `canAdd: false` with `error: "cannot create note because it is a duplicate"` is routed to the update path instead of the create path. The `options.allowDuplicate: true` flag exists but we deliberately do not use it — creating a true duplicate is never what we want, since our deduplication already ran at the pipeline level.

##### Conflict resolution when the user has edited a card in Anki

This is the interesting case: the user opened Anki, changed `UsageNotes` on a card, and two weeks later the pipeline re-runs and wants to update that same card from updated source material. We use a **three-way check**:

- `base` = the card fields we last synced (stored on `CardRecord.ankiweb_last_synced_fields`).
- `remote` = the fields currently on Anki's side (from `notesInfo`).
- `local` = the fields we want to push.

Merge rules, per field:

| base vs remote | base vs local | Action |
|---|---|---|
| same | same | No-op. |
| same | different | Push `local` (normal update). |
| different | same | Keep `remote` — the user edited; we don't overwrite. |
| different | different | **Conflict.** Default: keep `remote`, record conflict in `SyncReport.conflicts`, tag the note with `conflict:<card_id>` so the user can find it. Configurable to "always prefer local" or "always prefer remote" per source set. |

This is Option-C friendly too: the agent does exactly the same computation locally using state snapshots the server sends it.

##### Idempotency for retries

Every outgoing note carries a `req_id` (UUID) in `options.req_id` — which AnkiConnect ignores, but which we include in the tag `req:<req_id>` so retries can detect "this request already succeeded, do nothing" by searching for the tag. Cheap and doesn't depend on AnkiConnect remembering anything across restarts.

#### 13.3.7 Option C in detail: the pull-based desktop agent

The cloud service (Lambda) never initiates a connection to the user's home network. Instead, a small agent on the user's desktop owns the AnkiConnect conversation. The server just hosts a work queue.

##### Components

```
Pipeline (Lambda)                             User's desktop
──────────────────────────            ─────────────────────────────
 StateStore ──► /api/ankiweb      ⟵──  anki-agent (long-running)
                  delta feed              │
                                          ├─► localhost:8765 (AnkiConnect)
                                          │        │
                                          │        └─► desktop Anki
                                          │                │
                                          │                └─► AnkiWeb sync
                                          │
                                          └─► POSTs /api/ankiweb/ack
```

##### Protocol

Single-user at planning time, multi-user ready via `user_id`.

```
GET /api/ankiweb/pending?agent_id=<id>&cursor=<opaque>
Authorization: Bearer <agent-token>

200 OK
{
  "cursor":    "<next cursor>",
  "batch_id":  "<uuid>",
  "items": [
    {
      "op":      "create" | "update" | "verify" | "retire",
      "card_id": "<uuid>",
      "anki":    {
        "deckName":  "Chinese::301",
        "modelName": "Chinese vocabulary",
        "fields":    { "Simplified": "...", ... },
        "tags":      ["ext_id:<card_id>", "req:<req_id>", "auto-generated"],
        "options":   { "allowDuplicate": false }
      },
      "base_fields": { ... }           // for conflict detection on update/verify ops
    },
    ...
  ]
}
```

```
POST /api/ankiweb/ack
{
  "batch_id":  "<uuid>",
  "agent_id":  "<id>",
  "results": [
    {
      "card_id":      "<uuid>",
      "op":           "create" | ...,
      "status":       "applied" | "skipped" | "conflict" | "error",
      "anki_note_id": 1682340923122,               // set on create/update
      "error":        null | "<message>",
      "conflict": {                                // set when status == conflict
        "fields": ["UsageNotes"],
        "chosen": "remote"
      }
    }, ...
  ],
  "sync_requested": true,           // whether the agent called AnkiConnect's `sync` action
  "sync_status":    "ok"            // or "failed: <reason>"
}
```

The server updates `CardRecord.ankiweb_note_id`, `ankiweb_last_synced_at`, and `ankiweb_last_synced_fields` when it receives `status: applied`. For `status: conflict`, it additionally records the conflict in `SyncReport` and tags the card so it surfaces in the next `/api/ankiweb/pending` response for inspection.

##### Cursor semantics

`cursor` is an opaque token on the server; under the hood it's a timestamp + `card_id` tiebreaker. The server treats `pending?cursor=<X>` as "give me cards where `last_updated_at > X` OR (`last_updated_at == X` AND `card_id > tiebreaker`)". Clients never move the cursor on their own; it only advances on a successful `ack`. That way partial application is safe: a crashed agent that got the batch but didn't `ack` will re-fetch the same batch next poll.

##### Poll frequency and presence

- Agent polls `/api/ankiweb/pending` every 60 seconds when idle, every 5 seconds for 2 minutes after a successful apply (to catch follow-up batches), then backs off.
- If there are no pending items, the server returns `items: []` and the agent costs us ~1 API Gateway request / minute — negligible.
- On launch the agent calls AnkiConnect's `version` and `requestPermission`; if Anki isn't running, it exponentially backs off with a max 5-minute sleep. This handles laptops that are sometimes closed.

##### Desktop Anki sync handoff

After applying a non-empty batch, the agent calls AnkiConnect's `sync` action. This tells desktop Anki to push to AnkiWeb using the user's already-configured credentials; our agent never handles AnkiWeb passwords. The `sync_status` field in the ack lets the server surface "cards applied locally but AnkiWeb sync pending/failed" cleanly in the dashboard.

##### Agent packaging

- Distributed as a single-file Python script (the agent is ~300 lines; no heavy deps). Uses `httpx` + stdlib.
- Installer targets:
  - macOS: `launchd` plist template.
  - Linux: `systemd --user` unit template.
  - Windows: `schtasks /Create /SC ONLOGON` or a WinSW wrapper.
- Configuration file in `~/.config/anki-notes-pipeline/agent.toml`: server URL, agent token, AnkiConnect URL, poll cadence.
- Agent ships inside this repo under `scripts/ankiweb-pull-agent/` and has its own tiny test suite that stubs both AnkiConnect and the server.

##### Security on the agent API

- Agent token is a long opaque string, minted by the operator via `anki-notes-pipeline auth agent mint --agent-id <id>`, stored in DynamoDB as a bcrypt hash, never logged. Tokens are revocable independently of provider credentials.
- `/api/ankiweb/pending` and `/ack` live on the same custom domain as the Drive webhook and use mutual TLS as a future option (not day-one).
- Rate limits: 10 rps burst, 2 rps sustained per agent token. Legitimate traffic is nowhere near these limits.

#### 13.3.8 Initial deck bootstrap

First run against an empty AnkiWeb account has different semantics from steady-state:

1. `deckNames` → if the configured deck is missing and `auto_create_deck: true`, call `createDeck`.
2. `modelNames` → if the configured note type is missing and `auto_create_note_type: true`, call `createModel` with the schema listed in §13.3.5.
3. Issue `addNotes` in batches of 50.
4. Call `sync`.

If either auto-create flag is `false` (the default for `note_type`, because users often want to plug into their existing note types), a missing deck or model aborts the export with a clear remediation message rather than silently creating something unexpected.

#### 13.3.9 Mapping `CardRecord` → Anki note

| `CardRecord` field | Anki field | Notes |
|---|---|---|
| `simplified` | `Simplified` | First field; used by Anki's default duplicate check. |
| `traditional` | `Traditional` | |
| `pinyin` | `Pinyin` | Already normalized by `pinyin_normalize.py`. |
| `meaning` | `Meaning` | HTML-escaped before pushing; line breaks become `<br>`. |
| `part_of_speech` | `PartOfSpeech` | |
| `usage_notes` | `UsageNotes` | HTML-escaped like `Meaning`. |
| `first_seen_source_id` | `SourceRef` | Traceability: "which lesson did this come from?" |
| `card_id` | `ExtId` | Hidden but visible in Anki browser; also replicated as tag. |
| `card_id` | tag `ext_id:<card_id>` | Primary handle for `findNotes`. |
| `enrichment_version` | tag `enr:<version>` | For future selective re-enrichment (§12.6). |
| Run identifier | tag `run:<YYYY-MM-DD>` | Aids auditing; the user can filter "cards from last Friday's run" in Anki. |
| `first_seen_source_id` | tag `src:<source_id>` | Same, but by document. |

All our tags are in our own `ext_id:` / `enr:` / `run:` / `src:` / `conflict:` / `req:` namespaces — we never write general-purpose tags (e.g. `chinese`, `grammar`) onto the user's deck, to avoid colliding with their own tagging scheme.

#### 13.3.10 Failure modes and recovery

| Failure | Symptom | Handling |
|---|---|---|
| AnkiConnect not reachable (add-on off, Anki closed) | TCP connection refused to `:8765`. | Agent: back off up to 5 min. Server: no-op; items stay pending. |
| `apiKey` mismatch | `error: "valid api key must be provided"` | Fail loudly on agent startup; do not silently apply. |
| `addNote` duplicate first-field | Handled upstream by `canAddNotesWithErrorDetail`, but belt-and-braces catch: if AnkiConnect still returns duplicate, route that card through the update path. | |
| Note-type field mismatch (user edited note type) | `addNotes` succeeds but field counts don't match. | Pre-flight `modelFieldNames` check fails fast with a clear "update your note type" message. |
| `updateNoteFields` 404 (note deleted by user) | `error: "note was not found: <id>"` | Clear our `ankiweb_note_id`, re-lookup via tag, then `addNote` if truly missing. |
| `sync` fails (user not logged in, network issue) | `error: "this action is not supported yet" / "sync failed"` | Record in ack as `sync_status: "failed"`. Cards are still locally applied; next run calls `sync` again. |
| Agent crashes mid-batch | Some notes applied, none acked. | Server re-serves the same batch on next poll; idempotency tag `req:<req_id>` stops double-apply. |
| AnkiWeb rejects sync (conflict with another client) | Desktop Anki's sync prompt: full-sync required. | Out of scope for our automation; agent logs and alerts the user. We do not touch the user's collection in this case. |
| DynamoDB write fails after agent applies | Agent has applied but server doesn't know yet. | Agent retries ack with exponential backoff for 15 min; if the server eventually returns success, state converges. If not, next run's `notesInfo` check shows the applied state and realigns. |

#### 13.3.11 Observability and reporting

Every ack becomes a row in `SyncReport.exports[ankiweb]`:

```
{
  "run_id":      "<uuid>",
  "exporter":    "ankiweb",
  "agent_id":    "desktop-laptop",
  "batch_id":    "<uuid>",
  "created":     7,
  "updated":     2,
  "unchanged":   41,
  "conflicts":   1,
  "errors":      0,
  "sync_status": "ok",
  "duration_ms": 3412
}
```

Surfaced in the FastAPI `/api/sync/runs/{id}` response so a human can see exactly what landed on AnkiWeb per run.

CloudWatch alarms (Lambda mode):

- `/api/ankiweb/ack` error rate > 5% in 15 min → notify.
- Pending queue depth (cards with `ankiweb_last_synced_at < last_updated_at`) > 500 for > 24 h → notify ("agent probably hasn't run in a while").
- Per-agent last-poll-at older than 6 h during expected-online windows → notify.

#### 13.3.12 Testing strategy

- **AnkiConnect client unit tests** use a fake HTTP server that replays canned responses for each action our exporter exercises. No Anki install needed.
- **Pull-agent tests** stub both ends: the cloud-side `/pending` endpoint is mocked with FastAPI's `TestClient`, and AnkiConnect is mocked with the same fake server as above.
- **End-to-end manual loop**: spin up desktop Anki with AnkiConnect, point the agent at a local `uvicorn` instance running the FastAPI app against a SQLite `StateStore`, run `run_incremental_sync` against a small seed set, and verify the notes appear in Anki and then on AnkiWeb after a desktop sync.
- **Property tests** for the three-way merge (§13.3.6): random base/remote/local triples fed to the merge function, check invariants (idempotent on reapply, never loses a user edit that the base doesn't know about).

#### 13.3.13 What we would need from an official AnkiWeb API

For completeness, if AnkiWeb ever exposes a first-party upload API the exporter reduces to a much simpler shape. The interface we'd want is essentially:

- OAuth 2.0 user-delegated auth.
- `POST /decks/{deck_id}/notes` (create), `PATCH /notes/{id}` (update), `GET /notes?tag=...` (lookup), `DELETE /notes/{id}` (delete — we'd still not use this).
- A server-side `ext_id` field to replace our tag-based correlation.
- Bulk variants with partial failure reporting (matching AnkiConnect's `canAddNotesWithErrorDetail` semantics).

Until that exists, Option C + AnkiConnect is the path.

#### 13.3.14 Where should the Anki desktop instance live?

Assuming the pipeline lives in the cloud (Lambda), the desktop-Anki+AnkiConnect stack still has to live somewhere. Three realistic homes:

##### Option H1 — On the user's existing local machine, privately (no public exposure)

**This is the design §13.3.7 already assumes.** Because the pull agent polls outbound to the cloud, AnkiConnect on the laptop never needs a public address, an open port, a reverse proxy, or a DNS record. The laptop makes egress HTTPS calls only; inbound traffic is the cloud responding to those polls. It works behind any NAT, firewall, or carrier-grade NAT with zero network configuration, and passes every corporate-network + hotel-wifi scenario.

| Dimension | Rating |
|---|---|
| Always-on cost | $0 (uses a machine the user already owns and runs). |
| Ongoing ops cost | Zero day-to-day. Agent is a ~300-line script installed once. |
| Network config | None. Outbound-only. |
| Security surface | Tiny: AnkiConnect bound to `127.0.0.1` (default), only the agent process on that same host talks to it. |
| Latency cards-landing-on-AnkiWeb | Seconds to ~minute when the laptop is online; next login when it's closed. |
| Hard requirement | Desktop Anki must be running at some point for cards to land — exactly the same requirement as a human user clicking "Sync". |
| Works on mobile-only users? | No — but neither do any of the alternatives. |

##### Option H2 — On the user's local machine, publicly accessible (reverse direction)

This is what you *would* need if the cloud pipeline called AnkiConnect directly instead of the agent polling. It's worth documenting so we can explicitly reject it.

To make it work you'd need:

- A public hostname (dynamic DNS or static IP).
- Port forwarding on the router (or a reverse tunnel like `ngrok` / `cloudflared`).
- TLS termination (ACME certs on the laptop, or via the tunnel provider).
- An authentication layer on top of AnkiConnect's weak `apiKey` — realistically a reverse proxy (`nginx`, Caddy) with OAuth/mTLS, because exposing a shared-secret-only endpoint to the public internet is not defensible.
- Firewall ACLs on the laptop restricting which source IPs can hit the tunnel.
- A story for the laptop's dynamic IP, sleep/wake cycles, travel, corporate wifi that blocks inbound tunnels, carrier-grade NAT on cellular fallback, etc.

| Dimension | Rating |
|---|---|
| Always-on cost | $0–$5/mo (DDNS, possibly a paid tunnel). |
| Ongoing ops cost | **High** — every moving part above is a future breakage. Cert renewals, tunnel drops, router firmware resets. |
| Network config | Invasive; sometimes impossible (hotels, airports, many employer networks). |
| Security surface | **Bad.** AnkiConnect's `apiKey` is a single shared secret with no rotation story, no per-request signing, no audit log; you're bolting a real auth layer on top of an add-on that wasn't written with public exposure in mind. |
| Latency | Excellent when it works. |
| Hard requirement | Anki must be running *and* the tunnel must be up *and* the laptop must have routable egress. |

**Rejected** — all the cost of running a server plus all the security risk of exposing a desktop. The pull-agent design removes the only reason you'd ever want this.

##### Option H3 — On a separate cloud server running Anki headlessly

A small persistent VM (EC2 `t4g.small`, Hetzner CX11, etc.) running desktop Anki under a virtual display (`xvfb`) with AnkiConnect enabled. The cloud pipeline writes to this instance, and it in turn syncs to AnkiWeb.

| Dimension | Rating |
|---|---|
| Always-on cost | ~$5–10/mo (the cheapest VM that can run the Anki GUI under Xvfb without swapping). Violates the "no always-on resources" rule unless we're willing to make an exception for this. |
| Ongoing ops cost | **Highest.** Anki is a GUI app forced into a headless role; it breaks in unusual ways (Qt platform plugin failures, display-server upgrades, AppImage/Flatpak path surprises). Requires keeping Anki itself updated so AnkiConnect stays compatible. |
| Network config | Moderate. Inbound reachable only from the pipeline's VPC or via signed requests, similar to our other Lambda endpoints. |
| Security surface | Moderate. `apiKey` plus mTLS between Lambda and the VM is defensible. |
| Latency | Seconds. |
| Hard requirement | Anki process stays up; sync runs on schedule or on push. |
| Multi-device Anki | **Breaks badly.** If the user also runs desktop Anki on their laptop, both instances will sync to the same AnkiWeb account and Anki's full-sync-required prompt will appear intermittently. Fixable only by making the cloud Anki the sole writer, which means the user loses the ability to edit notes on their own desktop Anki app. |
| Cloud/serverless version | Not viable. Anki is a GUI app with persistent on-disk state (`collection.anki2`) and can't be containerized into a stateless Lambda. You can shove it into a container on ECS/Fargate but you're still paying for it 24/7 because cold-start of the Anki GUI under Xvfb is slow and fragile, and the `.anki2` file has to live on a persistent EFS or EBS volume. |

##### Recommendation

**H1 — run the AnkiConnect instance on your existing local machine, no public exposure.** The pull-agent protocol in §13.3.7 is specifically designed to make this the path of least resistance: no port forwarding, no DDNS, no certs, no reverse proxy, no special hardware, and no always-on cost beyond the machine you already own. The only real constraint is that desktop Anki needs to be running at some point for cards to propagate to AnkiWeb — but that's the same constraint every Anki user already lives with (review days still require an open Anki at some point).

H2 should be avoided entirely. It trades a clean polling design for a large, permanent security and ops burden.

H3 is only the right answer if the user has no machine of their own that runs Anki daily (e.g. mobile-only Anki setup, but in that case AnkiWeb isn't the user's primary endpoint either) **or** has an explicit need for near-zero latency between pipeline output and AnkiWeb propagation. Neither applies to the stated lesson-notes workflow, where cards are consumed the next evening or later.

##### What changes depending on the choice

- **H1 (recommended):** no changes to §13.3.7. The agent ships as a pre-written script; the user runs a one-liner installer.
- **H2 (not recommended):** would require adding an inverse exporter mode `ankiweb_push` that calls AnkiConnect directly from Lambda, plus a whole network-verification dance (DNS, TLS, reverse proxy) that lives entirely outside our codebase. We don't plan this path in.
- **H3:** same agent code as H1, just running on a headless VM instead of a laptop. The plan keeps H3 as a documented fallback but doesn't build any H3-specific tooling beyond a `cloud-init` example so the user can stand one up if they must.

##### Summary table

| | H1 — your laptop, private (selected) | H2 — your laptop, public | H3 — headless cloud VM |
|---|---|---|---|
| Monthly cost | $0 | $0–5 | $5–10+ |
| Network setup | None | DDNS/tunnel + certs + proxy | Standard VPC |
| Security burden | Minimal (`127.0.0.1`-only) | Severe | Moderate |
| Ops burden | Near-zero | High and ongoing | Highest (headless GUI) |
| Works on restrictive networks | Yes | No | Yes |
| Multi-device Anki friendly | Yes | Yes | No |
| Sync latency | Seconds–minutes when online | Seconds | Seconds |
| Requires Anki running | Yes | Yes | Yes, 24/7 |
| Aligns with §11–§12 invariants | Yes | Yes | Yes |
| Aligns with "no always-on cloud" rule | Yes | Yes | **No** |

#### 13.3.15 H1 implementation details (selected)

This subsection pins down exactly what gets built to realize H1 end-to-end: the local-machine side (Anki + AnkiConnect + the pull agent) and the cloud side (the pull-agent endpoints, DynamoDB keys, and secrets it consumes). It is a plan, not an implementation.

##### 13.3.15.1 User-visible artifacts

Two things get shipped to the user:

1. A **one-shot setup CLI command** (part of our existing `anki-notes-pipeline` package) that walks them through installing AnkiConnect, minting an agent token, and installing the agent under whichever OS init system they use.
2. A **standalone pull-agent script** installed into `~/.local/share/anki-notes-pipeline/agent/` (macOS/Linux) or `%LOCALAPPDATA%\AnkiNotesPipeline\agent\` (Windows), with an accompanying init-system unit file.

Everything else (credentials, state, logs) is in well-known per-user paths — no shared system locations, no `sudo`.

##### 13.3.15.2 Directory layout on the user's machine

```
~/.local/share/anki-notes-pipeline/        # macOS/Linux (XDG_DATA_HOME)
  agent/
    agent.py                      # the pull agent (single file)
    requirements.txt              # pinned deps: httpx, tenacity (and stdlib)
    venv/                         # created by installer, isolated from user's Python
  cache/
    last_cursor                   # opaque cursor as a plain text file
    inflight_batches/             # staged batches we received but haven't fully acked
      <batch_id>.json
    applied_log.ndjson            # append-only log of what we've applied (observability)

~/.config/anki-notes-pipeline/              # XDG_CONFIG_HOME
  agent.toml                      # user-editable config (see §13.3.15.4)
  agent.token                     # agent bearer token, chmod 0600

~/.local/state/anki-notes-pipeline/         # XDG_STATE_HOME
  agent.log                       # rotating log file (10 MB × 3)
```

Windows equivalents: `%LOCALAPPDATA%\AnkiNotesPipeline\{agent,cache,config,state}`.

##### 13.3.15.3 Setup command: `anki-notes-pipeline agent setup`

Interactive, idempotent, safe to re-run. Flow:

1. **Detect Anki.** Attempt `GET http://127.0.0.1:8765/` (AnkiConnect responds with literal string `AnkiConnect`). If absent, print the canonical install instructions (AnkiWeb shared add-on code `2055492159`) and exit.
2. **Verify AnkiConnect version.** `POST {"action":"version","version":6}`; require `>= 6`. Warn below `23.10.29.0`.
3. **Check AnkiConnect config.** Call `getProfiles`/`getActiveProfile` to confirm a profile is selected. Inspect `webBindAddress` via `getConfig` if available; refuse to continue if it isn't `127.0.0.1` (if the user has intentionally exposed AnkiConnect we surface a clear "this is H2, not H1" error).
4. **Request permission.** `POST {"action":"requestPermission","version":6}`. Expect `{permission:"granted"}`. If AnkiConnect's popup permission prompt is enabled, instruct the user to approve it.
5. **Pre-flight the deck/model.** For each source set with an AnkiWeb exporter: verify the deck exists (auto-create with explicit consent if configured) and that the note type has the expected fields (§13.3.5). If the user has an existing note type with a different field order, offer a mapping file override.
6. **Mint an agent token.** `POST https://<our-api>/api/ankiweb/agent/register` with the OAuth login the user already has for the pipeline. Server returns `agent_id` and one-time bearer `token`. Both are written to `~/.config/anki-notes-pipeline/agent.token` (chmod 0600).
7. **Write `agent.toml`** with sensible defaults (§13.3.15.4).
8. **Install the init-system unit.** OS-specific (§13.3.15.7). The installer never writes to `/etc/` or `%SystemRoot%`; everything stays under the user account.
9. **Start the agent**. Tail 30 lines of `agent.log`; confirm one successful `/api/ankiweb/pending` poll; confirm it reports `anki_connect_ok: true`. Print a success summary.

All steps are also exposed individually for scripting: `agent setup --step verify-anki`, `agent setup --step mint-token`, etc.

##### 13.3.15.4 Agent configuration (`agent.toml`)

```toml
[server]
base_url    = "https://agent.anki-notes-pipeline.example.com"
agent_id    = "desktop-laptop"
token_file  = "~/.config/anki-notes-pipeline/agent.token"

[anki_connect]
url         = "http://127.0.0.1:8765"
api_key     = ""                          # empty = not configured in AnkiConnect
timeout_s   = 15
# If Anki isn't running, how long to back off before next probe.
startup_probe_interval_s = 30
startup_probe_max_s      = 300

[polling]
idle_interval_s   = 60                     # when /pending was empty last time
active_interval_s = 5                      # after a successful non-empty apply
active_window_s   = 120                    # how long to stay in "active" mode
jitter_ratio      = 0.2                    # +/- 20% randomization to avoid thundering herd
max_backoff_s     = 900                    # on repeated failures

[sync]
request_ankiweb_sync_after_batch = true
# If true, agent aborts if desktop Anki reports "full sync required" instead
# of attempting to choose a direction. User must resolve in Anki's UI.
abort_on_full_sync_conflict = true

[conflict]
policy = "prefer-remote"                   # "prefer-remote" | "prefer-local" | "tag-and-skip"

[logging]
level     = "info"
path      = "~/.local/state/anki-notes-pipeline/agent.log"
max_bytes = 10485760
backups   = 3
```

All values are overridable via environment variables with prefix `ANKI_AGENT_` (flat keys: `ANKI_AGENT_POLLING_IDLE_INTERVAL_S=120`).

##### 13.3.15.5 Agent runtime behavior

The agent is a single-threaded event loop that alternates between four states:

```
    ┌───────────── start ─────────────┐
    ▼                                 │
 WAITING_FOR_ANKI ──detect── IDLE ◀───┤
        ▲                    │        │
        │           empty    │ batch  │
        │           pending  │ received
        │                    ▼        │
        │                 APPLYING ───┘
        │                    │
        │   any error        │
        └────── back-off ────┘
```

State transitions:

- **WAITING_FOR_ANKI**: AnkiConnect probe fails. Back off with jitter, up to `startup_probe_max_s`. No cloud traffic in this state.
- **IDLE**: AnkiConnect is healthy. `GET /api/ankiweb/pending?cursor=<last>` every `idle_interval_s`. Empty response → stay idle.
- **APPLYING**: Non-empty batch received.
  - Stage batch to `cache/inflight_batches/<batch_id>.json` first (crash safety).
  - For each item, run the corresponding AnkiConnect action (§13.3.5).
  - Compute the three-way merge locally (§13.3.6) using the `base_fields` the server sent.
  - Collect per-item results.
  - After all items applied, optionally call AnkiConnect `sync`. Capture `sync_status`.
  - `POST /api/ankiweb/ack` with the full `results[]` + `sync_status`.
  - On successful ack, delete `cache/inflight_batches/<batch_id>.json` and write the new cursor to `cache/last_cursor`.
  - Append a summary line to `applied_log.ndjson`.
  - Enter ACTIVE polling (§13.3.15.6).
- **Back-off** (any state): exponential with full jitter, capped at `max_backoff_s`. Three consecutive ack failures with the same `batch_id` escalate to a log warning and a stderr message; we never drop the batch — we keep retrying.

Concurrency: strictly one inflight batch at a time per agent. The server's FIFO cursor guarantees no parallel batches are ever issued, but the agent enforces it locally too by refusing to fetch a new batch while `inflight_batches/` is non-empty.

##### 13.3.15.6 Active-polling heuristic

After a non-empty ack, the agent switches to `active_interval_s` polling for `active_window_s`, then decays back to `idle_interval_s`. Motivation: lesson-notes runs tend to produce several batches in quick succession (different source files settling around the same time). Active polling catches the cascade without costing anything during quiet periods. At `idle_interval_s = 60`, the steady-state cost to API Gateway is ~1 request/minute per agent — negligible.

##### 13.3.15.7 Init-system integration

The agent is installed as a per-user long-running service so it starts at login and restarts on crash.

###### macOS — `launchd`

`~/Library/LaunchAgents/com.anki-notes-pipeline.agent.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key>        <string>com.anki-notes-pipeline.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/USER/.local/share/anki-notes-pipeline/agent/venv/bin/python</string>
    <string>/Users/USER/.local/share/anki-notes-pipeline/agent/agent.py</string>
  </array>
  <key>RunAtLoad</key>    <true/>
  <key>KeepAlive</key>
    <dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key>  <string>Background</string>
  <key>StandardOutPath</key>
    <string>/Users/USER/.local/state/anki-notes-pipeline/agent.stdout</string>
  <key>StandardErrorPath</key>
    <string>/Users/USER/.local/state/anki-notes-pipeline/agent.stderr</string>
  <key>EnvironmentVariables</key>
    <dict><key>ANKI_AGENT_CONFIG</key>
          <string>/Users/USER/.config/anki-notes-pipeline/agent.toml</string></dict>
</dict></plist>
```

Loaded with `launchctl bootstrap gui/$(id -u) <plist>`. Survives reboots; restarts automatically on non-zero exit.

###### Linux — `systemd --user`

`~/.config/systemd/user/anki-notes-pipeline-agent.service`:

```ini
[Unit]
Description=Anki Notes Pipeline pull agent
After=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/share/anki-notes-pipeline/agent/venv/bin/python \
          %h/.local/share/anki-notes-pipeline/agent/agent.py
Environment=ANKI_AGENT_CONFIG=%h/.config/anki-notes-pipeline/agent.toml
Restart=always
RestartSec=5s

[Install]
WantedBy=default.target
```

Enabled with `systemctl --user enable --now anki-notes-pipeline-agent`. Plus `loginctl enable-linger $USER` so the agent runs even when the user isn't logged in graphically — optional and off by default, because Anki itself is a GUI app the user probably launches interactively anyway.

###### Windows — Task Scheduler

A registered task with trigger `At log on of user <USER>`, action `pythonw.exe %LOCALAPPDATA%\AnkiNotesPipeline\agent\agent.py`, restart on failure, hidden window. Distributed as a `.xml` task definition applied with `schtasks /Create /XML <path> /TN "AnkiNotesPipelineAgent"`.

##### 13.3.15.8 Ensuring Anki is actually running

The agent does not start Anki for the user. That's deliberate: the user already knows when they want Anki open, and launching it automatically (especially on macOS where Anki is a `.app`) surprises people and interferes with password managers / autolaunch settings.

Instead, the agent exposes its current state via two signals the user can check:

- `anki-notes-pipeline agent status` — prints health (`AnkiConnect reachable`, last successful poll, pending batches in-flight, pending count from last poll).
- Optional desktop notification on first transition from `WAITING_FOR_ANKI` → `IDLE` in a given day ("Anki-Connect reachable; 12 pending cards applied"). Off by default, on via `notifications.enabled = true`.

For the narrow case where the user *does* want auto-launch, we document a 3-line snippet for each OS (e.g. on macOS, a second launchd item that runs `open -a Anki` at login) rather than baking it into the agent itself.

##### 13.3.15.9 Security properties (the upside of H1)

- **AnkiConnect never leaves the loopback interface.** `webBindAddress` stays at `127.0.0.1`. The agent connects via `http://127.0.0.1:8765`; no TLS is needed because the traffic never leaves the kernel's loopback.
- **Only the agent process reaches AnkiConnect.** The laptop's OS firewall can safely drop all inbound traffic to port 8765 (and already does by default when bound to `127.0.0.1`).
- **Agent token is a narrow-purpose credential**, scoped to `/api/ankiweb/*` only, revocable independently of the user's OAuth session. Stored at `chmod 0600`.
- **Outbound-only traffic** means no router or corporate firewall configuration on the user's end. Hotels, coffee shops, and employer networks just work.
- **No inbound DNS, no ACME certs, no reverse proxy** — the moving parts that make H2 a nightmare are all absent.
- **AnkiConnect's `apiKey` is still set** even though it isn't strictly needed for loopback. Defense-in-depth, and it matches what the installer offers as a default for consistency with H3 deployments.

##### 13.3.15.10 Cloud-side additions (Lambda/FastAPI)

The H1 choice adds three endpoints and one DynamoDB table shape to the server side. None of this is new architecture — just pinning down names and schemas.

Endpoints (under `/api/ankiweb/`):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/ankiweb/agent/register` | Authenticated by user's OAuth; mints an agent token, returns `{agent_id, token}`. Idempotent on `agent_id`. |
| `POST` | `/api/ankiweb/agent/revoke` | Revokes a token by `agent_id`. |
| `GET`  | `/api/ankiweb/pending` | Returns next batch for this agent (§13.3.7 format). Requires `Authorization: Bearer <token>`. |
| `POST` | `/api/ankiweb/ack` | Acks a batch, advances cursor and updates `CardRecord.ankiweb_*` fields. |

DynamoDB records (added to §12.1):

| Record | Keys | Purpose |
|---|---|---|
| `AgentRecord` | `pk=agent#<user_id>`, `sk=<agent_id>` | Stores bcrypt-hashed token, `created_at`, `last_seen_at`, `last_poll_at`, `last_batch_id`, `last_sync_status`, `revoked_at`. |
| `PendingSyncCursor` | `pk=sync_cursor#<user_id>`, `sk=<agent_id>` | Per-agent cursor (timestamp + `card_id` tiebreaker). |

Existing `CardRecord` already carries `ankiweb_note_id`, `ankiweb_last_synced_at`, `ankiweb_last_synced_fields` (§12.1 + §13.3.6), so no change there.

##### 13.3.15.11 What we ask the user to do, concretely

The complete happy-path bootstrap from the user's perspective:

1. Install AnkiConnect from Anki's Tools → Add-ons dialog using code `2055492159`, restart Anki.
2. From a terminal on the same machine, run `anki-notes-pipeline agent setup` and follow the interactive prompts (logs into the pipeline OAuth, mints a token, writes config, installs the login-item/service).
3. Leave Anki running whenever they want cards to propagate. That's it.

Subsequent lesson note processing causes cards to appear in Anki (and, after the next desktop-Anki AnkiWeb sync, on AnkiWeb) without any further action on the user's part.

##### 13.3.15.12 Observability for the user

Three surfaces:

- `anki-notes-pipeline agent status` — local, immediate.
- `/api/sync/runs/{run_id}` (already in §13.3.11) — shows, per run, how many cards landed on which agent and what the `sync_status` was.
- Optional weekly email digest summarizing: runs completed, cards created/updated, conflicts (with links to Anki notes the user should review), any stuck agent (haven't seen a poll in >48 h).

##### 13.3.15.13 Failure modes unique to H1 (and how we handle them)

| Situation | Detection | Handling |
|---|---|---|
| User's laptop is closed for a week | No polls from the agent. | Cards accumulate in the `PendingSyncCursor` queue. When the laptop comes back online the agent catches up in one or more batches. `SyncReport.export.ankiweb.latency_p95` will spike; alert threshold `48h` is documented. |
| Agent's venv breaks (e.g. user upgrades system Python and the isolated venv points at a missing interpreter) | Init system reports repeated failures. | `agent setup --step rebuild-venv` recreates the venv; we ship it as a recovery subcommand. |
| User runs `agent setup` twice | Server returns the existing `agent_id` and rotates the token; old token is revoked. | Idempotent by design. |
| User migrates to a new laptop | They run `agent setup` on the new machine; old `agent_id` can be revoked via `agent revoke --agent-id old-laptop`. | `AgentRecord.last_seen_at` makes it obvious which one is stale. |
| AnkiConnect add-on auto-updated, new version breaks compatibility | `version` handshake or an action fails at startup. | Agent pins a known-good minimum AnkiConnect version in `agent.toml` and prints a clear remediation message; we also publish a compatibility matrix in the agent's README. |
| Desktop Anki is running in a different profile than the one the user set up against | `deckNames` returns a profile-scoped list; the expected deck is missing. | Agent fails loudly with "Anki is open under profile X but exporter was configured for profile Y." No silent writes to the wrong profile. |
| User manually edited AnkiConnect's `webBindAddress` to `0.0.0.0` | Detected by `getConfig` on startup. | Agent warns ("this is H2; you no longer need to expose AnkiConnect") but continues working; exit code remains 0. |
| Full-sync-required prompt appears in Anki | `sync` action returns a specific error. | Agent returns `sync_status: "full-sync-required"` in the ack; server surfaces this prominently in the UI. Agent does **not** attempt to answer the prompt. |

##### 13.3.15.14 Uninstall

`anki-notes-pipeline agent uninstall` removes the init unit, deletes the venv and cache, and calls `POST /api/ankiweb/agent/revoke`. Config and token files are preserved by default (common: users reinstall and want their existing config back); `--purge` deletes those too.

##### 13.3.15.15 Implementation sequence delta

This subsection slots cleanly into the existing **Phase 7 — New export targets** from §15 without inventing a new phase. Specifically §15.7d, "Implement pull-agent endpoints and a sample desktop agent script," expands to:

| Step | Change | Risk |
|---|---|---|
| 7d.1 | Implement `POST /agent/register`, `POST /agent/revoke`, `AgentRecord` DynamoDB model. | Low. |
| 7d.2 | Implement `GET /pending` and `POST /ack` with cursor semantics per §13.3.7. | Medium — cursor + batch idempotency must be tested carefully. |
| 7d.3 | Build the `agent.py` single-file script (no heavy deps). | Medium — three-way merge + AnkiConnect interaction + crash-safe batch staging. |
| 7d.4 | Ship init-system templates (launchd plist, systemd unit, Task Scheduler XML). | Low. |
| 7d.5 | Build the `agent setup` / `agent status` / `agent uninstall` CLI subcommands. | Low. |
| 7d.6 | Agent integration tests with a fake AnkiConnect + FastAPI `TestClient`; property tests for the three-way merge. | Low. |
| 7d.7 | User-facing documentation in `docs/users/ankiweb-agent.md` with screenshots for the three OS installers. | Low. |

No change is needed to Phase 5/6/8 from this subsection.

### 13.4 AnkiWeb exporter responsibilities

Regardless of option:

- Operate on `CardRecord`s with `ankiweb_last_synced_at is None or < last_updated_at`.
- Maintain `ankiweb_note_id`, `ankiweb_last_synced_at`, and `ankiweb_last_synced_fields` in `StateStore` after successful sync.
- Report per-card outcome in `ExportResult` so a user can see "added 3, updated 1, skipped 47, conflicts 1".
- Apply §13.3.6 three-way merge; never silently overwrite a field the user edited directly in Anki.
- Use the `ext_id:<card_id>` tag as primary identity; treat `Simplified` as a non-unique user-editable field.

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

---

## 17. Story Breakdown for Implementation

Everything in §§2–16 is decomposed below into independently implementable stories. Each story lists:

- **Prerequisites** — which other stories must land before this one is safe to merge.
- **Scope** — what code changes are in scope.
- **Out of scope** — explicit non-goals, to prevent drift.
- **Testing & verification** — unit, integration, and manual steps a reviewer can run to confirm correctness before merge.
- **Acceptance criteria** — the observable behavior that must hold after this story is complete.

Stories are grouped into six epics matching the phases already introduced in §§8 and 15. Each story produces a working, testable state of the system — a halfway-merged series leaves the main branch buildable and the existing CLI unbroken.

### 17.0 Conventions that apply to every story

- No story merges with failing tests or a broken CLI `run` subcommand.
- Every story adds unit tests covering its happy path and at least one failure path.
- Every story updates `README.md` or a file under `docs/` to document user-visible changes.
- Breaking changes to the on-disk / on-wire schema require a bump of `schema_version` on the affected records and a migration test.
- "Manual verification" steps assume a developer with local AWS credentials and a local Anki install (where relevant); each step lists how to skip it if those aren't available.

#### 17.0.1 Script-mode continuity invariant (applies to every story, every epic)

The user depends on the existing CLI-driven script flow and will continue to use it while serverless infrastructure is being set up. Every story in this plan — not just Epic A — must satisfy the following invariant at the moment it merges:

1. **`anki-notes-pipeline run <input> --output <csv> [--cedict-path ...]` must continue to work**, with the same command-line surface that exists on `main` today, with no new required flags, and without requiring any of the new subsystems to be configured (no StateStore, no web server, no source-set YAML, no Drive auth, no Lambda, no AnkiConnect). Passing zero configuration beyond what works today must still produce a CSV.
2. **Byte-for-byte output parity with `main`** on a fixed set of fixtures, established once at the start of Epic A (see §17.1.0) and re-checked by every subsequent story. A diff against the baseline is a merge blocker. The only exception is a story that deliberately changes output, which must update the baseline in the same commit and call out the change in the PR body.
3. **No new hard dependencies** introduced by later stories may become required for script mode. Dependencies for web/AWS/AnkiWeb/XLSX live in optional extras groups (`[server]`, `[aws]`, `[ankiweb]`, `[xlsx]`, etc.), and `pip install .` without extras must still give a working script-mode install.
4. **No `ImportError` on a barebones install.** The CLI entry point cannot top-level-import modules that depend on optional extras. Imports for those subsystems are deferred inside the subcommands that need them.
5. **No `main`-branch breakage between story merges.** Each story is shaped so the CLI still works after it lands, independently of whether later stories have merged.

A CI check (added in story A1 and kept green thereafter) enforces items 1, 2, and 4 automatically. Items 3 and 5 are enforced by review.

### 17.1 Epic A — Core library refactoring (maps to Phase 1)

These stories are pure-local, no cloud, no integrations. They must all land before anything in Epics B–F is safe to start. **Every story in Epic A is a pure internal refactor: the public CLI behavior does not change.**

#### 17.1.0 Script-mode baseline fixture and CI gate (landed as part of Story A1)

Before any refactor touches production code paths, Epic A establishes a baseline artifact set that every subsequent story is compared against. This is how we enforce §17.0.1 mechanically rather than by inspection.

**What the baseline contains:**

- `tests/baselines/inputs/`: at least one fixture per supported format (`sample.pdf`, `sample.md`, `sample.docx`). Small enough to run end-to-end in under ~10 seconds with the LLM **mocked**.
- `tests/baselines/outputs/`: the CSV each fixture produces, generated once on the tip of `main` before A1 is started, checked in verbatim.
- `tests/baselines/settings.env`: the exact settings used to generate the baseline (chunk size, skip-lines filter, csv_bom, etc.).
- `tests/baselines/llm_mock.json`: a recorded deterministic LLM response per chunk so subsequent runs produce identical output without actually calling Bedrock.

**The CI gate:**

A new test module `tests/test_script_mode_baseline.py` runs on every PR and does three things:

1. For each `(fixture, settings)` pair, invoke `anki-notes-pipeline run` as a **subprocess** (so we exercise the real CLI entry point, not the library directly) against a mocked LLM and assert the emitted CSV file is byte-for-byte identical to the baseline.
2. `pip install .` into a fresh temp virtualenv **without any extras**, then invoke the same CLI with `--help` and `run --help`. Assert both succeed (exit 0) and that no import warnings or `ImportError`-adjacent messages appear in stderr.
3. Import the top-level `anki_deck_generator` package in that bare venv and verify it does **not** transitively import FastAPI, boto3, openpyxl, `googleapiclient`, or any other extras-gated dependency. (Implementation: import the package in a subprocess with those modules replaced by a `MetaPathFinder` that raises on resolution; a successful import proves no top-level dependency leak.)

If any of these three checks fails, the PR is blocked.

**Updating the baseline:**

Intentional output changes require the PR to:
- Regenerate the baseline CSV with the new expected output, checked in as part of the same commit.
- Note the change prominently in the PR description ("Baseline updated because: ...").
- Cite the user-visible behavior change in the `CHANGELOG` entry.

This keeps accidental regressions noisy and deliberate changes reviewable.

**What "script mode" means for this invariant:**

- Entry point: the `anki-notes-pipeline` console script installed by `pyproject.toml`, invoked from a shell.
- Minimum runtime requirements: Python 3.12, AWS creds for Bedrock (as today), optionally a CEDICT file.
- No other services running (no FastAPI server, no DynamoDB, no AnkiConnect, no agent, no local SQLite file).
- `settings.env` or environment variables supply all configuration, exactly as on `main` today.

This is the shape the user depends on while serverless infrastructure is being set up in parallel, and it remains supported for the lifetime of the project — not just during Epic A.

---

#### Story A1 — Bytes-based ingest + script-mode baseline & CI gate

**Prerequisites:** none.

**Scope:**
- Add `extract_text_from_bytes(data: bytes, *, format: str) -> str` in `ingest/router.py`.
- Add bytes-accepting helpers in `ingest/pdf.py`, `ingest/markdown.py`, `ingest/docx.py`.
- Refactor `extract_text_from_path` to be a thin wrapper around `extract_text_from_bytes`.
- **Establish the §17.0.1 script-mode baseline**: create `tests/baselines/` with fixtures, recorded LLM mock responses, baseline CSVs (generated on the pre-refactor commit), and a settings env.
- **Add the CI gate** `tests/test_script_mode_baseline.py` per §17.1.0 that enforces byte-for-byte CSV parity, bare-venv install, and no extras-gated top-level imports.
- **Pin optional-extras groups** in `pyproject.toml`: existing deps stay in `[project.dependencies]` only if they are required for script mode; any that are only needed by web/AWS/AnkiWeb/XLSX subsystems move to their own extras groups (`[server]`, `[aws]`, `[ankiweb]`, `[xlsx]`). If none of those dependencies exist yet in `main`, the groups are declared empty and populated by later stories.

**Out of scope:**
- Any new file formats.
- Any pipeline or CLI user-visible behavior changes — this story preserves behavior while adding test infrastructure and an internal alternative entry point.

**Testing & verification:**
- Unit: for each existing ingestor, add a test that reads a fixture file into bytes and asserts `extract_text_from_bytes(...)` returns the same string as `extract_text_from_path(fixture_path)`.
- Unit: `extract_text_from_bytes(b"...", format="unknown")` raises `IngestError`.
- Regression: the full existing test suite still passes (`pytest`).
- **Baseline (CI)**: `tests/test_script_mode_baseline.py` runs `anki-notes-pipeline run` as a subprocess against each `tests/baselines/inputs/` fixture with the mocked LLM enabled, and diffs the output against `tests/baselines/outputs/`. Must be byte-identical.
- **Bare install (CI)**: a CI job creates a scratch virtualenv, runs `pip install .` (no extras), and then `anki-notes-pipeline --help` + `anki-notes-pipeline run --help`. Both succeed.
- **Import isolation (CI)**: in a subprocess where `fastapi`, `boto3`, `openpyxl`, and `googleapiclient` resolve to a raising `MetaPathFinder`, `import anki_deck_generator` must succeed. Fails if any of those are transitively imported at package top level.
- Manual: `anki-notes-pipeline run <real-fixture.pdf> --output /tmp/x.csv` with real Bedrock credentials still produces the same CSV as the commit immediately before A1 lands.

**Acceptance criteria:**
- All existing tests pass.
- New bytes-based entry points exist and are covered.
- `extract_text_from_path` no longer contains format-specific parsing logic (it delegates).
- The three CI gates (baseline, bare install, import isolation) are green and blocking on PRs.

---

#### Story A2 — `run_pipeline_from_text` + `PipelineResult`

**Prerequisites:** A1.

**Scope:**
- Introduce `PipelineResult` and `PipelineStats` dataclasses in `pipeline.py`.
- Extract `run_pipeline_from_text(text, settings, *, progress_callback=None) -> PipelineResult`.
- Refactor `run_pipeline(input_path, output_csv, settings)` into a thin wrapper: read file → `extract_text_from_bytes` → `run_pipeline_from_text` → `write_vocabulary_csv`.

**Out of scope:**
- Persistence, any StateStore awareness.
- Any export-target changes beyond CSV.

**Testing & verification:**
- Unit: call `run_pipeline_from_text(fixture_text, settings_with_mocked_llm)` and assert `result.rows` matches the golden output we use in `test_pipeline_e2e_mocked.py`.
- Unit: assert `progress_callback` is called with `("ingest", 1, 1)`, `("chunk", N, N_total)`, `("llm", N, N_total)`, `("export", 1, 1)` in order.
- Regression: `test_pipeline_e2e_mocked.py` still passes with no modification.
- **Baseline (CI)**: the baseline gate from A1 must still be green — byte-for-byte CSV parity across all fixtures.
- **Bare install (CI)**: still green — `run_pipeline_from_text` must not pull in any extras-gated modules.
- Manual: running `anki-notes-pipeline run <real-fixture>` against a real fixture with real Bedrock still produces a byte-identical CSV vs. `main`.

**Acceptance criteria:**
- `run_pipeline_from_text` is pure: no file writes, no stdin reads.
- `run_pipeline` behavior is unchanged from a user's perspective.
- §17.0.1 invariant holds: the CLI still runs against the same fixtures with the same output, with no new required flags or dependencies.

---

#### Story A3 — `vocabulary_csv_bytes` and `Exporter` protocol

**Prerequisites:** A2.

**Scope:**
- Add `Exporter` Protocol in `export/base.py`.
- Add `vocabulary_csv_bytes(rows, *, bom=False) -> bytes`.
- Wrap existing CSV writer as `CsvExporter` implementing `Exporter`.
- Wire `run_pipeline` to go through `CsvExporter` (not a direct function call).

**Out of scope:**
- XLSX and AnkiWeb exporters (separate stories).

**Testing & verification:**
- Unit: `CsvExporter.export` produces the same bytes as the legacy `write_vocabulary_csv` for several fixtures (including BOM on/off).
- Unit: `vocabulary_csv_bytes(rows, bom=True)` begins with `b"\xef\xbb\xbf"`.
- Regression: `test_csv_writer.py` passes unchanged.
- **Baseline (CI)**: still green — the Exporter protocol indirection must not change emitted bytes.

**Acceptance criteria:**
- No caller of the old `write_vocabulary_csv` exists outside the wrapper shim.
- §17.0.1 invariant holds: CLI still writes the same CSV bytes to disk for the same inputs.

---

#### Story A4 — Structured exception hierarchy

**Prerequisites:** none (can land in parallel with A1–A3).

**Scope:**
- Add `errors.py` with `AnkiPipelineError`, `IngestError`, `LlmError`, `IntegrationError`, `AuthenticationError`.
- Convert existing raw `raise` sites in `ingest/`, `llm/`, and `dictionary/enrich.py` to use the new hierarchy.

**Out of scope:**
- API-layer error translation (that's a later story).

**Testing & verification:**
- Unit: each existing error-raising code path now raises a subclass of `AnkiPipelineError`.
- Regression: CLI still prints a human-readable error when given an unsupported file type (verify by invoking `anki-notes-pipeline run not-a-real.xyz`).
- **Baseline (CI)**: still green — error-hierarchy changes must not alter happy-path CSV output.
- Manual: invoke the CLI against each baseline fixture and confirm identical output; invoke the CLI with an invalid path and confirm the human-readable error matches the pre-A4 behavior in spirit (same exit code, same non-zero semantics).

**Acceptance criteria:**
- No bare `raise Exception(...)` in `src/anki_deck_generator/` after this story.
- §17.0.1 invariant holds: CLI behavior (including error reporting) remains user-compatible.

---

### 17.2 Epic B — Persistent state layer (maps to Phase 5)

**Script-mode continuity in this epic:** every new CLI subcommand (`state init`, `state list-cards`, `state list-runs`) is **additive**. The existing `anki-notes-pipeline run` flow is unchanged and continues to operate with no StateStore configured. The §17.1.0 baseline CI gate remains green across every B-story merge.

---

#### Story B1 — `StateStore` protocol and records

**Prerequisites:** A4 (uses the error hierarchy).

**Scope:**
- Add `state/records.py` dataclasses: `SourceRecord`, `ChunkRecord`, `CardRecord`, `DriveChannelRecord`, `PendingEdits`, `AgentRecord`, `PendingSyncCursor`, `RunReportRecord`. Each carries a `schema_version: int = 1`.
- Add `state/store.py` with `StateStore` Protocol per §12.3.
- No implementations yet (that's B2/B3).

**Out of scope:**
- Any real storage backend.
- Migration logic.

**Testing & verification:**
- Unit: `dataclasses.asdict()` round-trip each record type.
- Unit: assert the Protocol signatures match the method set from §12.3 (using `typing.get_type_hints`).
- Static: `mypy` passes on the new modules.

**Acceptance criteria:**
- The Protocol is importable and typecheck-clean.

---

#### Story B2 — `SqliteStateStore`

**Prerequisites:** B1.

**Scope:**
- Implement `state/sqlite_store.py`.
- One SQLite file per deployment; schema created on first open.
- CLI: `anki-notes-pipeline state init --db-path <path>` and `anki-notes-pipeline state list-cards`.

**Out of scope:**
- DynamoDB.

**Testing & verification:**
- Unit: in-memory SQLite (`:memory:`) round-trip for every record type.
- Unit: `upsert_card` returns `{created}` on first call, `{unchanged}` on identical repeat, `{updated}` on field change.
- Unit: `iter_cards_changed_since(ts)` respects the timestamp.
- Unit: concurrent writes from two threads serialize cleanly (SQLite's `BEGIN IMMEDIATE`).
- Manual: `anki-notes-pipeline state init --db-path /tmp/test.db && state list-cards --db-path /tmp/test.db` prints an empty table.

**Acceptance criteria:**
- Ship-quality for dev/test use; 100% of `StateStore` methods covered.

---

#### Story B3 — `DynamoStateStore`

**Prerequisites:** B2 (shares test fixtures for conformance testing).

**Scope:**
- Implement `state/dynamo_store.py` using single-table design: PK patterns like `source#<provider>`, `card#`, `channel#`, `pending#<source_set>`, `agent#<user_id>`, `run#`.
- Conditional writes for `upsert_card` (idempotent); conditional `UpdateItem` for `advance_drive_channel_token`.
- Helper module `state/dynamo_table.py` with the CloudFormation/CDK-agnostic table definition (hash key `pk`, range key `sk`, on-demand billing, TTL attribute `ttl_unix`).

**Out of scope:**
- IaC for the table itself — that lives in Epic F.

**Testing & verification:**
- Unit: mock DynamoDB with `moto`; replay the same conformance suite from B2.
- Unit: conditional-write collision test — two concurrent `advance_drive_channel_token` calls, one must fail with `ConditionalCheckFailedException`.
- Integration (optional, gated on `AWS_TEST_DYNAMO=1` env var): run the same suite against a real DynamoDB Local container.
- Manual: `aws dynamodb scan --table-name ...` after a local test run shows the expected records.

**Acceptance criteria:**
- `SqliteStateStore` and `DynamoStateStore` pass the same conformance suite.

---

#### Story B4 — `StateStore` selection and settings

**Prerequisites:** B2, B3.

**Scope:**
- Add `state_backend: Literal["sqlite", "dynamodb"]` to `Settings`.
- Factory `state.get_store(settings) -> StateStore`.
- `anki-notes-pipeline state list-runs` subcommand.

**Out of scope:**
- Wiring into the pipeline (that's C1).

**Testing & verification:**
- Unit: factory returns the right class for each setting.
- Regression: `anki-notes-pipeline run` works with a default (no state backend configured) — the factory returns a no-op store.

**Acceptance criteria:**
- Both backends are reachable from the CLI.

---

### 17.3 Epic C — Incremental sync (maps to Phase 6)

**Script-mode continuity in this epic:** the new `schedule` subcommand is additive; the existing `run` subcommand is untouched. Any code C1 adds under `sync/` must import lazily from pipeline entry points so that `import anki_deck_generator` in a bare venv still does not pull in YAML/Drive deps. The §17.1.0 baseline CI gate remains green.

---

#### Story C1 — `SyncReport` and `run_incremental_sync` over a cold StateStore

**Prerequisites:** A2, B4.

**Scope:**
- Add `sync/report.py` with `SyncReport`, `SyncRunOutcome`.
- Add `sync/orchestrator.run_incremental_sync(...)` per §12.5.
- Behavior with an empty StateStore must be byte-for-byte identical to `run_pipeline_from_text` followed by CSV export.
- New CLI: `anki-notes-pipeline schedule --source-set <name> --state-db <path>` for local dry runs.
- Source-set config loader in `config/source_sets.py` (YAML today; schema versioned).

**Out of scope:**
- Any integration providers beyond local filesystem.
- Chunk-level change detection (that's C2).

**Testing & verification:**
- Unit: `run_incremental_sync` with a fresh `SqliteStateStore` produces the same `CardRecord`s as a direct `run_pipeline_from_text` invocation on the same fixture.
- Unit: `SyncReport` serializes to JSON stably.
- Manual: define a tiny local-filesystem source-set YAML pointing at a fixture PDF; run `schedule` twice; second run should short-circuit at the document level.

**Acceptance criteria:**
- A cold run is behaviorally equivalent to the current `run_pipeline`.
- A warm re-run over unchanged inputs completes without any LLM calls (verified by a spy on `extract_vocabulary_from_chunk`).

---

#### Story C2 — Four-layer change detection

**Prerequisites:** C1.

**Scope:**
- `sync/change_detection.py` implementing document → content → chunk → card layers per §12.4.
- Stable chunk hashing that survives text normalization.
- New `ChunkRecord` writes with each processed chunk.

**Out of scope:**
- Re-enrichment passes (kept as a future option per §12.6).

**Testing & verification:**
- Unit: edit one chunk of a multi-chunk fixture; assert only that chunk is sent to the mocked LLM on the second run.
- Unit: hash stability — `chunk_sha256("...")` produces the same output across runs and across Python versions supported by the project.
- Unit: `content_sha256` short-circuits LLM calls when only document metadata differs.
- Integration: two-run E2E test — first run cards `{A,B,C}`, edit the chunk that produced `B` to produce `B'`, second run should upsert only `B'` and leave `A` and `C` untouched.

**Acceptance criteria:**
- Chunk-level dedup demonstrated in a test.
- `SyncReport.stats.chunks_skipped > 0` in the second run.

---

### 17.4 Epic D — Google Drive provider & scheduled/event execution (maps to Phase 4 + most of Phase 8)

**Script-mode continuity in this epic:** Google API client libs, FastAPI, uvicorn, and boto3 all land in their own optional extras groups (`[google-drive]`, `[server]`, `[aws]`). `pip install .` (no extras) continues to produce a working `run`-only install. New subcommands (`import`, `serve`, `auth google-drive`, `drive watch ...`) live behind lazy imports: invoking `run` must not load any of them. The §17.1.0 baseline CI gate remains green across every D-story merge and explicitly re-verifies that `fastapi`, `boto3`, and `googleapiclient` stay out of the top-level import graph.

---

#### Story D1 — Integration framework skeleton

**Prerequisites:** A4.

**Scope:**
- `integrations/base.py` (`IntegrationProvider` ABC, `ImportedDocument`, `ImportResult`).
- `integrations/registry.py`.
- CLI: `anki-notes-pipeline import <provider>` sub-command that can list registered providers.

**Out of scope:**
- Any real provider.

**Testing & verification:**
- Unit: register a toy `echo` provider in tests; assert CLI dispatch works.
- Unit: `get_provider("not-a-thing")` raises `IntegrationError`.

**Acceptance criteria:**
- Registry works; no real provider code yet.

---

#### Story D2 — Google Drive provider (service-account + OAuth refresh-token auth)

**Prerequisites:** D1, A1 (bytes ingest).

**Scope:**
- `integrations/google_drive.py` implementing `IntegrationProvider`:
  - `authenticate(credentials)` for both service-account JSON and OAuth refresh token.
  - `list_sources(folder_id=...)`.
  - `import_documents(file_ids=..., folder_id=...)` — handles Google Docs → DOCX export, native PDF/DOCX media downloads, text/markdown.
  - `get_revision(file_id)` for change detection.
- CLI: `anki-notes-pipeline auth google-drive` (OAuth flow) and `anki-notes-pipeline import google-drive --folder-id ... --output out.csv`.
- Optional extras group `[google-drive]` in `pyproject.toml`.

**Out of scope:**
- `changes.watch` (that's D4).
- Webhook handling.

**Testing & verification:**
- Unit: mock `googleapiclient.discovery` to test each code path (Google Doc → DOCX, native PDF, trashed file, missing permission).
- Unit: OAuth token exchange happy path + refresh path, using `responses`.
- Integration (operator-run only, skipped in CI): point at a real test Drive folder with one shared Google Doc; run `import google-drive`; verify a populated CSV emerges.

**Acceptance criteria:**
- Import from Drive works end-to-end in operator-run test.
- All mock-based tests pass in CI.

---

#### Story D3 — Local `schedule` command uses the provider

**Prerequisites:** C1, D2.

**Scope:**
- Wire `run_incremental_sync` to call `GoogleDriveProvider.import_documents` when the source-set config lists `provider: google-drive`.
- `--dry-run` flag that fetches metadata and computes the diff but doesn't invoke the LLM.

**Out of scope:**
- Anything Lambda-specific.

**Testing & verification:**
- Unit: fake provider returning canned `ImportedDocument`s; `run_incremental_sync` writes the expected `CardRecord`s.
- Manual: `anki-notes-pipeline schedule --source-set my-test --dry-run` prints a plan ("would process 2 docs, 14 chunks").

**Acceptance criteria:**
- A local cron entry can now drive the whole flow using this command.

---

#### Story D4 — Drive `changes.watch` client (no webhook server yet)

**Prerequisites:** D2.

**Scope:**
- Add `changes.getStartPageToken`, `changes.list` (paginated), `changes.watch`, `channels.stop` to `integrations/google_drive.py`.
- CLI: `anki-notes-pipeline drive watch register --source-set <name>` and `drive watch unregister --channel-id <id>`.
- Channel record persisted via `StateStore`.

**Out of scope:**
- The HTTPS webhook endpoint.
- Debouncing.

**Testing & verification:**
- Unit: mock all four API calls; verify pagination logic and cursor advance.
- Unit: renewal job picks up rows expiring in <48h.
- Manual: register a watch against a real Drive account with an `ngrok` URL; make an edit; verify `changes.list(pageToken=...)` returns it.

**Acceptance criteria:**
- Channel registration and cursor advance demonstrated against a real Drive account in manual testing.

---

#### Story D5 — FastAPI app skeleton + `/health` + `/api/sync/run`

**Prerequisites:** C1.

**Scope:**
- `web/app.py` FastAPI app factory with CORS, upload size limits.
- `web/dependencies.py` providing `Settings`, `StateStore`, and cached `DictionaryIndex`.
- `web/routes/health.py`, `web/routes/pipeline.py` (`POST /api/sync/run`).
- CLI: `anki-notes-pipeline serve --host 0.0.0.0 --port 8000`.

**Out of scope:**
- WebSocket progress.
- Drive webhook route.
- Integration routes.

**Testing & verification:**
- Unit: FastAPI `TestClient` for `/health` (200 and JSON body sanity) and `/api/sync/run` (multipart upload with a fixture, returns a `job_id` and a completed `PipelineResult`).
- Manual: `curl -F "file=@fixture.pdf" http://localhost:8000/api/sync/run` returns a 200 with a job result.

**Acceptance criteria:**
- Local server starts, serves the two endpoints, doesn't block while processing (uses thread pool).

---

#### Story D6 — Drive webhook endpoint + two-tier dispatch

**Prerequisites:** D4, D5, B3.

**Scope:**
- `web/routes/drive_webhook.py` implementing the `POST /drive/notifications` handler per §11.3.7 and §11.3.8.
- Shared-secret token verification, `X-Goog-Message-Number` dedupe.
- SQS FIFO producer keyed on `MessageGroupId=channel_id`.
- Worker Lambda handler (`lambda/handler_drive_changes_worker.py`) that reads the queue, paginates `changes.list`, filters mime types and folder ancestors, invokes `run_sync(only_file_ids=...)`.
- Channel renewal Lambda (`lambda/handler_watch_renewal.py`) on a daily EventBridge schedule.

**Out of scope:**
- Debouncing (that's D7).
- Lambda packaging/IaC (that's F1).

**Testing & verification:**
- Unit: header verification rejects missing/mismatched tokens with 401.
- Unit: `sync` resource-state returns 200 without enqueuing.
- Unit: message-number replay returns 200 without enqueuing.
- Unit: worker test paginates `changes.list` across two pages, advances cursor only on success.
- Manual (operator): `ngrok`-exposed local FastAPI receives real Drive pings; edited doc's ID appears in the SQS queue and the worker runs.

**Acceptance criteria:**
- End-to-end loop from Drive edit → webhook → worker → `StateStore` update works in manual testing.
- Response SLA <2s observed for the verify-and-enqueue path.

---

#### Story D7 — Edit-session debouncing (`PendingEdits` + poller)

**Prerequisites:** D6.

**Scope:**
- `sync/debounce.py` owning `PendingEdits` reads/writes.
- Debouncer Lambda (`lambda/handler_drive_debouncer.py`) consuming the existing SQS queue and writing/extending `PendingEdits` rows per §11.6.2.
- Polling Lambda (`lambda/handler_pending_edits_poll.py`) on a 1-minute EventBridge rule; scans `PendingEdits WHERE ready_at <= now` and invokes the existing worker from D6 with `only_file_ids=[...]`.
- Split of responsibilities: debouncer advances `pageToken`; worker advances `SourceRecord`.
- Default knobs: `quiet_minutes=10`, `max_delay_minutes=120`. Per-source-set overrides (we'll use 20/90 for the lesson-notes source set per §11.6.12).
- Force-process endpoint `POST /api/integrations/google-drive/force-process`.
- "[done]" filename sentinel support in the webhook verifier.

**Out of scope:**
- AnkiWeb changes.

**Testing & verification:**
- Unit: debouncer upsert extends `ready_at` on repeat.
- Unit: poller fires exactly once when `ready_at` passes, not twice.
- Unit: hard-deadline test — 10 notifications spaced `quiet_minutes - 1` apart over `max_delay_minutes`; exactly one worker run fires at the hard deadline.
- Unit: force-flag bypasses `ready_at`.
- Integration: simulate a 30-ping burst via the `simulate` CLI, assert exactly one worker run is scheduled.
- Manual: run against real Drive with a short `quiet_minutes=2`; edit a doc in sustained bursts; confirm a single run lands ~2 min after editing stops.

**Acceptance criteria:**
- Observed worker-run count per chatty session equals 1 (or 1-per-`max_delay_minutes`-window for pathological cases).
- `drive.debounce.fired` log line present with correct `reason`.

---

### 17.5 Epic E — Export targets (maps to Phase 7)

**Script-mode continuity in this epic:** `openpyxl`, AnkiConnect client code, and the pull-agent live behind `[xlsx]` and `[ankiweb]` extras. `run` never reaches any of this code. The baseline CI gate remains green and the import-isolation check is extended to include `openpyxl`.

---

#### Story E1 — XLSX exporter

**Prerequisites:** A3.

**Scope:**
- `export/xlsx_writer.py` implementing `XlsxExporter` with two sheets: `Vocabulary` (same columns as CSV) and `Run metadata` (from `SyncReport`).
- Optional extras group `[xlsx]` = `openpyxl`.
- Source-set config support for `type: xlsx`.

**Out of scope:**
- AnkiWeb anything.

**Testing & verification:**
- Unit: open the produced file with `openpyxl.load_workbook`, assert sheet names and row counts.
- Unit: with `openpyxl` not installed (`sys.modules` monkeypatch), importing the exporter raises a helpful `IntegrationError`.
- Manual: open the produced file in Excel/Numbers and verify formatting.

**Acceptance criteria:**
- A run with `exporters: [..., xlsx]` produces both a CSV and an XLSX side by side, identical vocabulary rows.

---

#### Story E2 — AnkiConnect client library

**Prerequisites:** A4.

**Scope:**
- `export/ankiweb/anki_connect.py` — thin wrapper around AnkiConnect v6: `version`, `requestPermission`, `deckNames`, `createDeck`, `modelNames`, `modelFieldNames`, `createModel`, `canAddNotesWithErrorDetail`, `addNotes`, `updateNote`, `updateNoteFields`, `updateNoteTags`, `findNotes`, `notesInfo`, `addTags`, `removeTags`, `sync`, `multi`.
- Request envelope, error-to-exception translation, optional `apiKey`.

**Out of scope:**
- Exporter logic (that's E3).
- Pull-agent wrapper (that's E4–E6).

**Testing & verification:**
- Unit: every wrapper method has a test that stubs `httpx` and asserts the correct JSON body and return value.
- Unit: `error != null` in an AnkiConnect response raises an `IntegrationError` subclass.
- Manual: against a local Anki with AnkiConnect, `python -m anki_deck_generator.export.ankiweb.anki_connect --action version` prints `6`.

**Acceptance criteria:**
- Every action listed in §13.3.5 has a thin, typed wrapper.

---

#### Story E3 — AnkiWeb exporter logic (identity + three-way merge)

**Prerequisites:** E2, C2.

**Scope:**
- `export/ankiweb/exporter.py` implementing `AnkiWebExporter` per §13.3.6 and §13.3.9.
- `ext_id:<card_id>` tagging, `req:<req_id>` idempotency tagging, `run:<date>` / `src:<source_id>` / `enr:<version>` metadata tags.
- Three-way merge with policy knob `prefer-remote|prefer-local|tag-and-skip`.
- Updates to `CardRecord.ankiweb_*` fields on success.

**Out of scope:**
- Any network-facing side of the pull agent.

**Testing & verification:**
- Unit: each row of the three-way merge matrix from §13.3.6 as a separate test (same/same, same/diff, diff/same, diff/diff with each policy).
- Property test: merge is idempotent when re-applied to its own output.
- Unit: `addNotes` returning a nil for one note triggers a single `findNotes`-then-`updateNote` fallback.
- Integration: against a stubbed AnkiConnect, export 100 fixture cards; assert correct counts in `ExportResult`.

**Acceptance criteria:**
- User edits in Anki are never silently overwritten (regression tested).

---

#### Story E4 — Cloud-side pull-agent endpoints

**Prerequisites:** E3, D5, B3.

**Scope:**
- `web/routes/ankiweb_agent.py` with `POST /agent/register`, `POST /agent/revoke`, `GET /pending`, `POST /ack`.
- `AgentRecord` and `PendingSyncCursor` in DynamoDB.
- Bearer-token middleware with constant-time comparison.
- Cursor semantics per §13.3.15.10.

**Out of scope:**
- The agent binary (that's E5).

**Testing & verification:**
- Unit: `/register` mints a token, returns `{agent_id, token}`, stores a bcrypt hash.
- Unit: `/pending` returns an empty batch with the same cursor when no `CardRecord.ankiweb_last_synced_at < last_updated_at` exists.
- Unit: `/pending` honors `since` and batch-size limits (default 50).
- Unit: `/ack` with a bogus `batch_id` returns 409.
- Unit: `/ack` advances cursor only if all items in the batch are `status ∈ {applied, conflict, skipped}`.
- Integration: happy-path round trip — register → pending → ack → `CardRecord.ankiweb_note_id` populated.

**Acceptance criteria:**
- Endpoints return the exact shapes specified in §13.3.7.

---

#### Story E5 — Pull agent (local H1 script) + init-system templates

**Prerequisites:** E2, E4.

**Scope:**
- `scripts/ankiweb-pull-agent/agent.py` per §13.3.15.5.
- `agent.toml` schema and loader.
- Init-system templates for macOS (`launchd`), Linux (`systemd --user`), Windows (Task Scheduler XML), under `scripts/ankiweb-pull-agent/init/`.
- CLI subcommands: `anki-notes-pipeline agent setup|status|uninstall|rebuild-venv|revoke`.

**Out of scope:**
- Auto-launching Anki.
- Packaging as a PyPI extra (optional follow-up).

**Testing & verification:**
- Unit: agent state machine transitions (WAITING_FOR_ANKI → IDLE → APPLYING → back to IDLE) driven by injected stubs.
- Unit: crash-recovery — create an inflight batch file, restart the agent, assert the batch is replayed via `POST /ack` without re-fetching.
- Integration: against a fake AnkiConnect server + a `TestClient`-backed FastAPI app, run the agent for one full cycle; assert the expected DynamoDB state.
- Manual (each OS):
  - Install AnkiConnect, run `anki-notes-pipeline agent setup`.
  - Trigger a pipeline run that creates 3 cards.
  - Within 60 seconds, observe 3 notes in Anki, tagged `ext_id:<uuid>`.
  - After the agent calls `sync`, observe the cards in AnkiWeb's web UI.

**Acceptance criteria:**
- End-to-end loop works on all three OSes.
- Agent survives a `kill -9` mid-batch with no data loss on restart.

---

#### Story E6 — AnkiWeb exporter observability

**Prerequisites:** E3, E4, E5.

**Scope:**
- `SyncReport.exports[ankiweb]` structured log per §13.3.11.
- `/api/sync/runs/{run_id}` surfaces per-run AnkiWeb results.
- CloudWatch alarms: `/ack` error rate, pending queue depth, silent agents.
- Optional weekly email digest (behind a config flag; off by default).

**Out of scope:**
- A UI; this is structured-log + alarm scope only.

**Testing & verification:**
- Unit: run-report shape matches §13.3.11.
- Unit: a simulated "agent not seen in 48 h" alarm fires.
- Manual: trigger a run, curl `/api/sync/runs/{id}`, verify the `exports.ankiweb` block.

**Acceptance criteria:**
- A user can see, from one HTTP call, exactly what landed on AnkiWeb per run.

---

### 17.6 Epic F — Serverless deployment (maps to Phase 8)

**Script-mode continuity in this epic:** nothing in Epic F is imported by the script-mode CLI. `infra/` and `lambda/` are packaged separately (the SAM build uses them; the PyPI-distributable package excludes them). `pip install .` still produces a working script-mode install with no Lambda/SAM/`sam-cli` requirements. The baseline CI gate remains green.

---

#### Story F1 — Lambda container image + bootstrap

**Prerequisites:** B3, C2.

**Scope:**
- `infra/lambda.Dockerfile` based on `public.ecr.aws/lambda/python:3.12`.
- `lambda/bootstrap.py` assembling `Settings`, `StateStore`, and lazy-loading CEDICT from S3 into `/tmp` with a global cache.
- `lambda/handler_schedule.py` (T1 from §11.1) delegating to `run_incremental_sync`.

**Out of scope:**
- Other handlers (covered by D6 and E4 code that runs inside these handlers).
- IaC (that's F2).

**Testing & verification:**
- Unit: bootstrap caches CEDICT across warm invocations (call it twice, assert S3 mock is hit once).
- Unit: `handler_schedule({"source_set": "x"}, ctx)` calls `run_incremental_sync` with the right args.
- Manual: build the image, run with `docker run -p 9000:8080 ...`, invoke via the Lambda Runtime Interface Emulator.

**Acceptance criteria:**
- Image builds under 2 GB; cold start for `handler_schedule` <10s on a `t3.micro` equivalent (measured).

---

#### Story F2 — IaC (AWS SAM or CDK — pick one)

**Prerequisites:** F1, D6, D7, E4.

**Scope:**
- One stack defining: Lambda function(s), EventBridge schedules (one per configured source set), API Gateway HTTP API with the routes from D5/D6/E4, DynamoDB table from B3, S3 export bucket, Secrets Manager secrets for Drive OAuth + agent tokens, SQS FIFO queue + DLQ, CloudWatch alarms, custom domain + ACM cert.
- Decision: **AWS SAM** — simpler than CDK for this shape, and the deliverable is a single template file + container push.

**Out of scope:**
- Multi-region deployments.

**Testing & verification:**
- Unit: `sam validate` passes.
- Integration: `sam local start-api` serves `/health` locally.
- Manual: `sam deploy --guided` to a dev AWS account; invoke the schedule Lambda manually; verify `SyncReport` appears in DynamoDB and CloudWatch Logs.

**Acceptance criteria:**
- One-command deploy from a clean account.
- Teardown via `sam delete` leaves no orphaned resources (verified by `aws resource-groups`).

---

#### Story F3 — CI/CD

**Prerequisites:** F2.

**Scope:**
- GitHub Actions workflow `.github/workflows/deploy.yml`: build image → push to ECR → `sam deploy`.
- Separate `.github/workflows/test.yml`: run `pytest` + `mypy` + `ruff` on every PR.
- Fallback scheduled workflow `.github/workflows/weekly-sync.yml` running the same container once on GitHub's cron, for users who don't want AWS.

**Out of scope:**
- Blue/green / canary deploys.

**Testing & verification:**
- Manual: push to a feature branch, confirm test workflow runs.
- Manual: merge to `main`, confirm deploy workflow runs against a staging account.

**Acceptance criteria:**
- New main commit auto-deploys to staging without human intervention.

---

#### Story F4 — End-to-end smoke test

**Prerequisites:** F2, F3, D7, E5.

**Scope:**
- A single scripted `tests/e2e/weekly-lesson.sh` that:
  1. Seeds a test Google Drive folder with a sample Google Doc.
  2. Runs `sam deploy` to a scratch account.
  3. Registers a Drive watch and an agent token.
  4. Edits the doc.
  5. Waits for debounce to fire.
  6. Runs a local agent against a local Anki with AnkiConnect.
  7. Asserts that the expected cards exist in Anki, tagged `ext_id:...`.
  8. `sam delete`.

**Out of scope:**
- Day-to-day CI (this is operator-run only, gated on an env var).

**Testing & verification:**
- Manual: a developer runs the script on their laptop with their own test AWS + Google accounts.

**Acceptance criteria:**
- Script runs clean on a fresh environment; teardown leaves nothing.

---

### 17.7 Dependency graph (visual)

```
       A1 ──► A2 ──► A3                (Epic A)
              │      │
              │      ▼
       A4 ────┴──► E1                  (Epic E)
        │
        ├─► D1 ──► D2 ──► D3            (Epic D providers)
        │          │       │
        │          ▼       │
        │          D4 ─────┤
        │                  ▼
        │                 D5 ──► D6 ──► D7
        │                         │       │
        ▼                         │       │
       B1 ──► B2 ──► B4 ──► C1 ──► C2    (Epics B, C)
                     │       │
                     ▼       │
                     B3 ─────┤
                             ▼
                            E2 ──► E3 ──► E4 ──► E5 ──► E6   (Epic E)
                                           │       │
                                           ▼       ▼
                                          F1 ──► F2 ──► F3 ──► F4   (Epic F)
```

Shortest useful slice to get *something* running end-to-end: A1 → A2 → A3 → A4 → B1 → B2 → B4 → C1 → D1 → D2 → D3. That alone gives you a locally-scheduled CLI that reads from Drive, persists to SQLite, and writes CSV — no Lambda, no AnkiWeb, no webhooks. Everything else is an add-on to that foundation.

### 17.8 Verification posture

Three levels of verification apply, consistently:

1. **Unit tests**, in every story, run on every PR. Hermetic, fast, deterministic.
2. **Integration tests**, most stories. Use `moto` for AWS, fake HTTP for AnkiConnect, FastAPI `TestClient` for web routes. No network, no real AWS, no real Anki. Run on every PR.
3. **Manual / operator-run checks**, listed per story. Require real credentials or a real Anki install. Not gated on CI; documented so a reviewer can run them before approval.

Every story's "Acceptance criteria" lists at least one observable property a reviewer can check without reading the diff. If a story doesn't have that, it's not ready to merge.

### 17.9 Recommended implementation order

The dependency graph in §17.7 only tells you what *can* land in what order. This subsection states what *should* land in what order, and why. The recommendation reflects three priorities, in this order:

1. **Preserve script-mode utility** (§17.0.1). The user keeps a working CLI throughout. No epic begins until script-mode parity infrastructure is in place.
2. **Deliver value early.** Each milestone unlocks a real new capability the user can use, rather than just preparing for one.
3. **Defer the hardest, most operationally-expensive work as long as possible**, so it benefits from the most context and gets only as much engineering effort as the value justifies.

#### 17.9.1 Recommended order across epics

```
Milestone 1: Script-mode safety net      → A1 → A4 → A2 → A3
Milestone 2: Local persistence           → B1 → B2 → B4 → C1 → C2
Milestone 3: Drive ingestion (still local) → D1 → D2 → D3
Milestone 4: AnkiWeb on user's desktop   → E2 → E3
Milestone 5: Cloud surface (web first)   → D5 → B3
Milestone 6: AnkiWeb agent loop          → E4 → E5 → E6
Milestone 7: XLSX export                 → E1
Milestone 8: Event-driven Drive          → D4 → D6 → D7
Milestone 9: Serverless deployment       → F1 → F2 → F3 → F4
```

Read the milestones as the order in which a single implementer should pull stories off the queue. The arrows inside each milestone show the within-milestone order.

#### 17.9.2 What each milestone unlocks for the user

| Milestone | After this lands, the user can… | Cost added | Risk if skipped |
|---|---|---|---|
| **M1** Script-mode safety net | Keep using `run` exactly as today, but with a CI gate that prevents future regressions. | None (refactor only). | Future stories silently break the CLI. |
| **M2** Local persistence + incremental sync | Re-run the pipeline weekly without re-LLM-ing unchanged content; persistent card inventory in SQLite; `state list-cards`. | One SQLite file. | Every later epic depends on this; skipping it forces full re-runs forever. |
| **M3** Drive ingestion (locally scheduled) | `anki-notes-pipeline schedule --source-set lessons` reads from Drive, processes only what changed, writes a CSV. Runs from `cron`/launchd today. | Google OAuth one-time. | Without this you're still uploading PDFs by hand. |
| **M4** AnkiWeb on user's desktop | Run the pipeline locally; cards land in desktop Anki via AnkiConnect; user clicks Sync (or has Anki sync automatically). | Install AnkiConnect; one local config. | AnkiWeb stays out of reach. |
| **M5** Cloud surface (web first) | FastAPI server runs locally (or on any box), accepts uploads, exposes status. DynamoDB-backed StateStore is now usable. | Optional self-hosting. | Without this you can't move toward serverless. |
| **M6** AnkiWeb agent loop | The pull-agent on your desktop polls the cloud server; cards from any pipeline run (local or remote) land in Anki automatically. | One init-system unit on the desktop. | AnkiWeb sync remains manual. |
| **M7** XLSX export | Get an audit-friendly XLSX alongside the CSV per run. | `openpyxl` install. | No functional impact; nice-to-have. |
| **M8** Event-driven Drive | Edit a Google Doc, debounce settles, cards appear minutes later — no cron. | Drive watch channel + custom domain. | M3's weekly cron is already enough for the stated workflow; this is latency improvement only. |
| **M9** Serverless deployment | The whole thing runs on Lambda; nothing on your laptop except the AnkiWeb agent. | AWS account + SAM stack. | Self-hosted server from M5 still works. |

#### 17.9.3 Within-epic order rationale

##### Epic A — `A1 → A4 → A2 → A3`

- **A1 first** because it lands the §17.0.1 baseline CI gate. Nothing else can be merged safely until that gate is green and protecting `main`.
- **A4 (errors) before A2 (pipeline split)** because `run_pipeline_from_text` is the natural place to start raising structured exceptions; if A4 lands later, A2 has to be reworked to thread the new error types through. Cheap to do A4 second.
- **A2 before A3** because the `Exporter` protocol (A3) is most naturally introduced when the caller is already a clean function (`run_pipeline_from_text`), not the legacy `run_pipeline`.

Net result of M1: behavior identical to today, with the CI gate enforcing it forever.

##### Epic B — `B1 → B2 → B4 → C1 → C2 → B3`

- **`B1 → B2 → B4`**: get a working local persistence story first using SQLite. This is the smallest possible change that makes incremental sync feasible.
- **`C1 → C2` immediately after B4**: incremental sync is what makes persistence actually useful. Land it before adding a second backend.
- **`B3` (DynamoDB) deferred to M5**: a second backend is dead weight until you actually need it (i.e., until something in the cloud writes to it). Building it earlier risks design drift, because the access patterns become clear only once C2 has run against real data for a few weeks.

The user's local SQLite-backed runs from M2 are forward-compatible: every record carries `schema_version`, and B3's DynamoDB schema is a strict superset of B2's, so cards from M2 are readable by M5 with a one-shot migration.

##### Epic D — `D1 → D2 → D3` first; `D4 → D6 → D7` deferred to M8

- The "ingest from Drive" half (D1–D3) is independently valuable: it gives you the weekly cron use case without any webhook infrastructure.
- The "react to Drive edits" half (D4–D7) requires a public HTTPS endpoint, custom domain, Search Console verification, debounce table, and SQS queue. None of that is needed for the stated weekly-lesson workflow, where a Friday cron is enough.
- Deferring D4–D7 to M8 means they are built only after the rest of the system is real, which is when their design choices (debounce window, queue topology) can be validated against actual usage data from M2–M3.

##### Epic E — `E2 → E3` in M4; `E4 → E5 → E6` in M6; `E1` in M7

- **`E2 → E3` first** lands AnkiConnect support that works *locally* with no cloud. This is the smallest possible AnkiWeb integration: you run the pipeline on your laptop, cards appear in Anki, you click Sync. Useful immediately.
- **`E4 → E5 → E6` deferred to M6** because the pull-agent loop only matters when the pipeline runs somewhere other than your laptop. Once you have a cloud surface (M5), the agent unlocks "pipeline anywhere → AnkiWeb everywhere."
- **`E1` (XLSX) is `M7` because it has the lowest functional value**. It's a nice-to-have for audit; nobody is blocked on it. Slipping it last keeps focus on the higher-value stories.

##### Epic F — strict `F1 → F2 → F3 → F4`

These are dependency-ordered with no flexibility: image (F1) → IaC (F2) → CI (F3) → smoke test (F4). Don't try to parallelize.

#### 17.9.4 Suggested merge cadence

This is per-story sizing, not calendar time:

| Milestone | Stories | Approximate size |
|---|---|---|
| M1 | 4 stories (A1, A4, A2, A3) | A1 is the largest in this set because of baseline + CI gate; the rest are small. |
| M2 | 5 stories (B1, B2, B4, C1, C2) | C2 is medium; the rest are small. |
| M3 | 3 stories (D1, D2, D3) | D2 is medium (Drive auth + Google API). |
| M4 | 2 stories (E2, E3) | E2 is small per-method but broad; E3 has the three-way merge logic. |
| M5 | 2 stories (D5, B3) | Both medium. |
| M6 | 3 stories (E4, E5, E6) | E5 (the agent itself + init-system templates) is the biggest single story in the project. |
| M7 | 1 story (E1) | Small. |
| M8 | 3 stories (D4, D6, D7) | D7 (debounce) is the trickiest of the three. |
| M9 | 4 stories (F1, F2, F3, F4) | F2 is the biggest; F4 is operator-run. |

Stories within a milestone are reviewable independently; a milestone is "done" when all its stories have merged and the milestone's user-visible capability has been manually verified end-to-end.

#### 17.9.5 What this order optimizes for, explicitly

- **Continuous CLI usability.** After M1, every subsequent merge keeps `anki-notes-pipeline run` working. After M3, the user has a functional cron-driven workflow. After M4, AnkiWeb is reachable. None of this requires AWS.
- **AWS dependency deferred to M5+.** Anything that requires an AWS account or paying for cloud resources is in M5 or later. The user can complete M1–M4 entirely on a single laptop.
- **No dead code.** Each story exists in service of a milestone that delivers something. There are no "build this now because we'll need it later" stories.
- **Reversibility.** The last 3 milestones (M7, M8, M9) are independently optional: if you decide the weekly-cron + local-AnkiConnect workflow from M3+M4 is enough, you can stop after M6 and still have a complete, supportable system.

#### 17.9.6 Where to deviate from this order (and where not to)

| Reason | Acceptable deviation? |
|---|---|
| Build XLSX (E1) earlier because someone wants it | Yes — E1 has no dependencies beyond A3. |
| Skip M2 and start on D2 | **No.** Drive ingestion without persistence re-LLMs everything every run. |
| Build the Lambda image (F1) before M5 | **No.** F1's bootstrap consumes `StateStore` and `run_incremental_sync`; both must exist and have been validated locally first. |
| Build E5 (the agent) before E4 (the endpoints) | No — agent has nothing to talk to. |
| Build the AnkiWeb session-cookie fallback (Option B from §13.3.3) | **Don't build it at all** unless §16 risk #1 forces the issue. |
| Land webhooks (D6/D7) before the local cron flow (D3) | No — webhooks change *how* runs are triggered; D3 establishes *what* a run does in the first place. |
| Switch state backend to DynamoDB (B3) earlier than M5 | Discouraged. DynamoDB Local works but adds dev-environment friction with no payoff until something in the cloud is writing to the table. |
