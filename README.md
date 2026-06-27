# Mandarin Anki deck generator

Pipeline: PDF / Markdown / DOCX → plain text → Amazon Bedrock (LangChain) → CC-CEDICT enrichment → vocabulary cards for Anki.

**Docs:** [Architecture & command map](docs/architecture.md) · [Change detection](docs/change-detection.md) · [E2E testing](docs/users/end-to-end-testing.md)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

```bash
cp .env_SAMPLE .env
```

Fill in AWS credentials for Bedrock (e.g. `AWS_BEARER_TOKEN_BEDROCK`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) in your actual `.env` file.

Download CC-CEDICT (`cedict_ts.u8`) from [MDBG CC-CEDICT](https://www.mdbg.net/chinese/dictionary?page=cc-cedict) and pass `--cedict-path`.

## Usage

```bash
anki-notes-pipeline run /path/to/notes.pdf --output out.csv --cedict-path /path/to/cedict_ts.u8
```

Recoverable failures (unsupported file type, bad LLM fixture, etc.) print a single `error: …` line on stderr and exit with code 1.

Library callers can invoke `run_pipeline_from_text` (pure text in → `PipelineResult` out) and keep filesystem/HTTP concerns in the caller; the CLI continues to use `run_pipeline` on paths. CSV bytes are produced through the `Exporter` protocol (`export/exporters.py`). Optional XLSX export (`pip install '.[xlsx]'`) adds `VocabularyXlsxExporter` with `Vocabulary` and `Run metadata` worksheets; configure `exporters` in a source-set YAML or use `VocabularyXlsxFileExporter` from incremental sync.

Options: `--chunk-size`, `--chunk-overlap`, `--csv-bom`, `--skip-lines-filter`, model params via environment (see `anki_deck_generator.config.settings`).

### Local state and incremental sync (optional)

Install the sync extra if you use YAML source sets: `pip install -e ".[sync]"` (PyYAML is also included in `[dev]`).

```bash
anki-notes-pipeline state init --db-path ~/.local/share/anki-notes-pipeline/state.db
anki-notes-pipeline state list-cards --db-path ~/.local/share/anki-notes-pipeline/state.db
anki-notes-pipeline state list-runs --db-path ~/.local/share/anki-notes-pipeline/state.db
```

Define `source_sets` in a YAML file (see `ANKI_PIPELINE_SOURCE_SET_CONFIG` or pass `--source-set-config`), then:

```bash
anki-notes-pipeline schedule --source-set myset --state-db /path/to/state.db \
  --source-set-config sources.yaml --output deck.csv --cedict-path /path/to/cedict_ts.u8
```

Re-running `schedule` on unchanged files skips ingest/LLM at the document level; edits reuse cached chunks when only part of a document changes. See [docs/change-detection.md](docs/change-detection.md).

### Sync to desktop Anki (local direct)

Install the AnkiConnect add-on in desktop Anki, keep Anki running, then either add an `anki` exporter to your source-set YAML or pass CLI flags:

```yaml
exporters:
  - type: anki
    deck_name: "Chinese::301"
    model_name: "Chinese vocabulary"   # optional; must match your Anki note type fields
```

```bash
pip install -e ".[sync,ankiweb]"

anki-notes-pipeline schedule \
  --source-set myset \
  --state-db ~/.local/share/anki-notes-pipeline/state.db \
  --source-set-config sources.yaml \
  --to-anki --anki-deck-name "Chinese::301" \
  --cedict-path /path/to/cedict_ts.u8
```

Cards land in Anki via AnkiConnect on `http://127.0.0.1:8765`. No web server or background agent is required for this path. The run report JSON includes `exports.anki` with created/updated counts.

For the optional cloud-shaped pull-agent path (pipeline and Anki on different hosts), see [docs/users/ankiweb-agent.md](docs/users/ankiweb-agent.md).

## Debug logging helper

The module `anki_deck_generator.debuglog` is kept in the repo as a small NDJSON logger you can use when diagnosing pipeline issues. By default, the pipeline does **not** emit debug logs; add temporary calls to `debug_log(...)` where needed and remove them after verification.

## Tests

```bash
pytest
```

### End-to-end manual testing

After Milestones 1–8, use the step-by-step guide with run commands and the personal `~/Documents/anki-pipeline-test-state.yaml` fixture: [docs/users/end-to-end-testing.md](docs/users/end-to-end-testing.md).

### Script-mode baseline (CI)

Regression tests under `tests/test_script_mode_baseline.py` compare CLI output to checked-in CSVs in `tests/baselines/outputs/` using a deterministic LLM stub. Set `ANKI_PIPELINE_LLM_FIXTURE_PATH` to `tests/baselines/llm_mock.json` (as CI does) so `anki-notes-pipeline run` does not call Bedrock. To refresh fixtures after intentional output changes, run `python tests/baselines/record.py` from the repo root with dev dependencies installed.

## Event-driven Google Drive (advanced / optional)

The default local Drive workflow is **`schedule` on a cron timer** — no webhooks required. Partial document changes are handled automatically (see [change-detection.md](docs/change-detection.md)).

The sections below add **near-real-time** processing via Drive push notifications. This requires `[google-drive]` + `[server]`, an HTTPS endpoint (ngrok for local dev), and is primarily for future cloud deployment (Epic F).

### Setup (requires `[google-drive]` extra + HTTPS endpoint)

1. **Authenticate**: `anki-notes-pipeline auth google-drive --client-secrets client_secret.json`
2. **Expose HTTPS endpoint** locally with [ngrok](https://ngrok.com/): `ngrok http 8000`
3. **Start the API server**: `anki-notes-pipeline serve`

### Register a watch channel

```bash
anki-notes-pipeline drive watch register \
  --source-set MY_SOURCE_SET \
  --webhook-url https://<ngrok-id>.ngrok.io/api/drive/notifications \
  --credentials-file ~/.config/anki-notes-pipeline/google-drive-token.json \
  --state-db ~/.local/share/anki-notes-pipeline/state.db
```

### Renew expiring channels (run daily or via EventBridge)

```bash
anki-notes-pipeline drive watch renew \
  --webhook-url https://<your-domain>/api/drive/notifications \
  --credentials-file ~/.config/anki-notes-pipeline/google-drive-token.json \
  --state-db ~/.local/share/anki-notes-pipeline/state.db
```

### Unregister a channel

```bash
anki-notes-pipeline drive watch unregister \
  --channel-id <channel-id-from-register> \
  --credentials-file ~/.config/anki-notes-pipeline/google-drive-token.json \
  --state-db ~/.local/share/anki-notes-pipeline/state.db
```

### Simulate a webhook locally (E2E without Google delivering the HTTP call)

```bash
anki-notes-pipeline drive webhook simulate \
  --channel-id <channel-id> \
  --state change \
  --state-db ~/.local/share/anki-notes-pipeline/state.db
```

### Run Mode B manually (process settled pending edits)

```bash
anki-notes-pipeline drive process-pending \
  --source-set MY_SOURCE_SET \
  --state-db ~/.local/share/anki-notes-pipeline/state.db \
  --source-set-config sources.yaml
```

### Source-set YAML with edit settling (webhook mode only)

When using webhooks (not plain `schedule` polling), tune the debounce window:

```yaml
schema_version: 1
source_sets:
  my-lessons:
    sources:
      - provider: google-drive
        folder_ids: ["<folder-id>"]
        credentials_file: ~/.config/anki-notes-pipeline/google-drive-token.json
    exporters:
      - type: csv
        destination: ~/deck.csv
    edit_settling:
      enabled: true
      quiet_minutes: 10       # wait 10 min of silence before processing
      max_delay_minutes: 120  # process anyway after 2 h of continuous edits
```

### Webhook flow overview

```
Drive edit → Google push → POST /api/drive/notifications
  → verify token → enqueue Mode A → changes.list → PendingEdits (debounced)
  → (quiet window elapses) → Mode B tick → run_incremental_sync(trigger="drive-push")
  → updated cards + exports
```
