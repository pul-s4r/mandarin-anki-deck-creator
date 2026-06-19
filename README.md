# Mandarin Anki deck generator

Pipeline: PDF / Markdown / DOCX → plain text → Amazon Bedrock (LangChain) → CC-CEDICT enrichment → vocabulary CSV for Anki.

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

Re-running `schedule` on unchanged files skips ingest/LLM at the document level; edits reuse cached chunks when only part of a document changes.

## Debug logging helper

The module `anki_deck_generator.debuglog` is kept in the repo as a small NDJSON logger you can use when diagnosing pipeline issues. By default, the pipeline does **not** emit debug logs; add temporary calls to `debug_log(...)` where needed and remove them after verification.

## Tests

```bash
pytest
```

### Script-mode baseline (CI)

Regression tests under `tests/test_script_mode_baseline.py` compare CLI output to checked-in CSVs in `tests/baselines/outputs/` using a deterministic LLM stub. Set `ANKI_PIPELINE_LLM_FIXTURE_PATH` to `tests/baselines/llm_mock.json` (as CI does) so `anki-notes-pipeline run` does not call Bedrock. To refresh fixtures after intentional output changes, run `python tests/baselines/record.py` from the repo root with dev dependencies installed.

## Event-driven Google Drive (Milestone 8)

M8 adds reactive processing via Drive push notifications: register watch channels, receive webhooks, debounce edits, and process settled changes through the incremental sync pipeline.

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

### Source-set YAML with edit settling

Add an `edit_settling` block to tune the debounce window:

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
