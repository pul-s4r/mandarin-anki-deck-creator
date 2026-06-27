# Architecture and command map

This document is the primary reference for **which command to run for which goal**. Deep dives: [change-detection.md](change-detection.md), [users/end-to-end-testing.md](users/end-to-end-testing.md).

## What the project does

Study notes (PDF, Markdown, DOCX, Google Drive) → vocabulary extraction (Bedrock LLM + optional CEDICT) → persistent card inventory → export to files and/or **desktop Anki**.

## Layers

```
┌─────────────────────────────────────────────────────────────┐
│  ENTRY POINTS (CLI, optional HTTP)                          │
│  run | schedule | serve | agent | drive                     │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  CORE PIPELINE (shared)                                     │
│  ingest → preprocess → LLM → enrich → StateStore            │
│  run_pipeline_from_text | run_incremental_sync              │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  DELIVERY (pick one or more)                                │
│  csv/xlsx files  |  anki (direct AnkiConnect)               │
│                  |  anki-agent (cloud pull agent — optional)│
└─────────────────────────────────────────────────────────────┘
```

## Which command for which goal

| Goal | Command | State DB? | Anki delivery |
|------|---------|-----------|---------------|
| One-shot: local file → CSV only | `run` | No | Manual CSV import |
| Incremental: local/Drive → state + CSV/XLSX | `schedule` | Yes | Optional `type: anki` in YAML or `--to-anki` |
| Incremental → desktop Anki directly | `schedule --to-anki --anki-deck-name "…"` | Yes | **Local direct** (AnkiConnect on same machine) |
| Inspect persisted cards / runs | `state list-cards`, `state list-runs` | — | — |
| Google Drive OAuth (once) | `auth google-drive` | — | — |
| Poll Drive on a timer (cron) | `schedule` with `google-drive` source | Yes | Any configured exporter |
| HTTP upload (optional) | `serve` + `POST /api/sync/run` | Yes | Pull agent if configured |
| Cards via background agent (cloud-shaped) | `serve` + `agent setup` | Yes | **Pull agent** — see [users/ankiweb-agent.md](users/ankiweb-agent.md) |
| Drive webhooks (advanced / cloud) | `serve` + ngrok + `drive watch` | Yes | See README “Event-driven Google Drive” |

## Local default workflow (recommended)

**Install:** `pip install -e ".[dev,sync,ankiweb]"` (add `[google-drive]` for Drive sources).

**1. One-time setup**

```bash
anki-notes-pipeline state init --db-path ~/.local/share/anki-notes-pipeline/state.db
# Optional for Drive:
anki-notes-pipeline auth google-drive --client-secrets client_secret.json
```

**2. Source-set YAML** (`sources.yaml`)

```yaml
schema_version: 1
source_sets:
  my-notes:
    sources:
      - provider: local-filesystem
        path: ~/Documents/lesson-notes.pdf
      # Or Google Drive:
      # - provider: google-drive
      #   folder_ids: ["YOUR_FOLDER_ID"]
      #   credentials_file: ~/.config/anki-notes-pipeline/google-drive-token.json
    exporters:
      - type: anki
        deck_name: "Chinese::301"
```

**3. Run sync** (Anki + AnkiConnect must be running)

```bash
anki-notes-pipeline schedule \
  --source-set my-notes \
  --state-db ~/.local/share/anki-notes-pipeline/state.db \
  --source-set-config sources.yaml \
  --cedict-path /path/to/cedict_ts.u8
```

**CLI shortcut** (without `type: anki` in YAML):

```bash
anki-notes-pipeline schedule ... --to-anki --anki-deck-name "Chinese::301"
```

**4. Cron for Drive** (poll for edits; partial chunk reuse is automatic)

```cron
0 * * * * anki-notes-pipeline schedule --source-set my-drive-set ...
```

## Entry points in detail

### `run`

- **Input:** single local file path
- **Output:** CSV file only
- **State:** none
- **Use when:** quick one-off export, no incremental history

### `schedule`

- **Input:** source-set name + YAML config + state DB
- **Output:** configured exporters (csv, xlsx, **anki**)
- **State:** reads/writes SQLite (`SourceRecord`, `ChunkRecord`, `CardRecord`, `RunReportRecord`)
- **Use when:** incremental sync from local files or Google Drive

### `state`

- **Subcommands:** `init`, `list-cards`, `list-runs`
- **Use when:** inspecting what the pipeline remembers

### `serve` (optional)

- **Requires:** `[server]` extra
- **Routes:** `/health`, `/api/sync/run`, `/api/sync/runs/{id}`, `/api/ankiweb/*`, `/api/drive/notifications`
- **Use when:** HTTP upload or pull-agent delivery; not required for local direct Anki

### `agent` (optional — cloud delivery)

- **Subcommands:** `setup`, `status`, `uninstall`, `rebuild-venv`, `revoke`
- **Use when:** pipeline runs separately from Anki (future cloud / Epic F); **not needed** if you use `type: anki` or `--to-anki` on the same machine

### `drive` (optional — event-driven / advanced)

- **Subcommands:** `watch register|renew|unregister`, `webhook simulate`, `process-pending`
- **Use when:** near-real-time Drive webhooks; **not required** for cron-based `schedule` polling
- See README “Event-driven Google Drive (Milestone 8)”

## Exit points and completion status

| Exit | Where to check “done” |
|------|------------------------|
| CSV / XLSX file | Path in `SyncReport.export_paths` or YAML `destination` |
| Desktop Anki (direct) | `SyncReport.exports.anki[]` with `created`/`updated` counts; notes in Anki with tag `ext_id:<card_id>` |
| Desktop Anki (agent) | Agent ack → same `exports.anki` block; see agent log |
| Run history | `state list-runs` or `runs` table in SQLite; full JSON in `sync_report_json` |

A finished `schedule` run prints JSON including `run_id`, `stats` (chunks processed/skipped, documents skipped), and `exports`.

## Observable internal state (SQLite)

| Table / record | Meaning |
|----------------|---------|
| `sources` | Last seen revision/hash per document |
| `chunks` | Per-chunk text hash + cached card IDs (LLM skip) |
| `cards` | Vocabulary inventory + Anki sync cursors |
| `runs` | Sync run reports (`trigger`, timestamps, JSON stats) |
| `drive_channels` | Watch channel metadata (webhook mode only) |
| `pending_edits` | Debounce queue (webhook mode only) |

Inspect with `sqlite3 $STATE_DB ".tables"` or commands in [users/end-to-end-testing.md](users/end-to-end-testing.md).

## Optional extras

| Extra | Enables |
|-------|---------|
| `[sync]` | YAML source sets (`schedule`) |
| `[ankiweb]` | Direct AnkiConnect + pull agent (httpx) |
| `[google-drive]` | Drive sources |
| `[xlsx]` | XLSX export |
| `[server]` | FastAPI `serve` |

Bare `pip install .` still supports `run` only (script mode).

## Cloud / deferred (Epic F)

Not required for local use:

- AWS Lambda handlers (`lambda_handlers/`)
- DynamoDB store (`state/dynamo_store.py`)
- Production HTTPS webhooks without ngrok

The same core (`run_incremental_sync`) is intended to run unchanged when Epic F lands.

## Related docs

- [change-detection.md](change-detection.md) — how “what changed” is detected
- [users/end-to-end-testing.md](users/end-to-end-testing.md) — manual test checklist
- [users/ankiweb-agent.md](users/ankiweb-agent.md) — pull-agent path (optional)
