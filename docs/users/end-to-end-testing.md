# End-to-end testing guide

Manual validation checklist for the full application after Milestones 1–8. Uses personal fixtures under `~/Documents` and the repo virtualenv.

## Assets

| Asset | Path | Purpose |
|-------|------|---------|
| Source-set config | `~/Documents/anki-pipeline-test-state.yaml` | Named `demo`, `demo-second`, `demo-multi`, `demo-drive`, `demo-drive-fast` |
| Active state DB | `~/Documents/anki-pipeline-test.db` | Primary DB for E2E |
| Prior state DB | `~/Documents/anki-pipeline-test-old.db` | Pre-E2E run (86 cards, real LLM) — restore if needed |
| Fresh state DB | `~/Documents/anki-pipeline-e2e-fresh.db` | Optional clean slate from Step 2 |
| Sample inputs | `~/Documents/mandarin-notes-sample-{1..4}.pdf` | Local pipeline inputs |
| LLM fixture (E2E) | `~/Documents/llm_mock_e2e.json` | Stub LLM responses for your PDFs (no Bedrock) |
| Export outputs | `~/Documents/demo-export.{csv,xlsx}` etc. | Written by `schedule` / Drive sync |

Repo reference copy of the YAML: [`docs/fixtures/anki-pipeline-test-state.yaml`](../fixtures/anki-pipeline-test-state.yaml).

## Progress

| Step | Description | Status |
|------|-------------|--------|
| 0 | Automated pytest gate | **Done** (220 passed) |
| 1 | CLI one-shot `run` | **Done** |
| 2 | State init (skip if using existing DB) | **Skipped** |
| 3 | Scheduled incremental sync | **Done** (3c skip verified; 3d export ok) |
| 4 | HTTP upload API | **Done** (`api-upload`) |
| 5 | Event-driven Drive | **Partial** — Mode B `drive-push` verified; live watch blocked (see below) |
| 6 | AnkiWeb agent (optional) | **Done** (1 pending item) |
| 7 | Final smoke checklist | **Done** |

## Shared environment

Run from the repo root after installing extras:

```bash
cd ~/CursorWS/mandarin-anki-deck-creator
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,sync,server,google-drive,xlsx,ankiweb]"
```

Set these in every terminal (or add to `.env`):

```bash
export REPO=~/CursorWS/mandarin-anki-deck-creator
export DOCS=~/Documents
export CLI=$REPO/.venv/bin/anki-notes-pipeline

export ANKI_PIPELINE_SOURCE_SET_CONFIG=$DOCS/anki-pipeline-test-state.yaml
export ANKI_PIPELINE_STATE_BACKEND=sqlite
export ANKI_PIPELINE_STATE_DB_PATH=$DOCS/anki-pipeline-test.db
export ANKI_PIPELINE_CEDICT_PATH=$REPO/tests/baselines/cedict_sample.u8

# Deterministic LLM (no Bedrock) — use personal fixture for ~/Documents PDFs (see below)
export ANKI_PIPELINE_LLM_FIXTURE_PATH=$DOCS/llm_mock_e2e.json

# AnkiWeb agent steps (optional)
export ANKI_SERVER_AGENT_REGISTER_SECRET=e2e-test-secret
```

### LLM fixture for your PDFs (run once before Steps 1 and 3)

`tests/baselines/llm_mock.json` only covers tiny CI inputs (`tests/baselines/inputs/sample.*`). Your `mandarin-notes-sample-*.pdf` files need a separate fixture or real Bedrock.

**Option A — personal fixture (recommended for E2E, no AWS):**

```bash
cd "$REPO"
.venv/bin/python tests/baselines/record.py \
  --add "$DOCS/mandarin-notes-sample-1.pdf" \
  --add "$DOCS/mandarin-notes-sample-2.pdf" \
  -o "$DOCS/llm_mock_e2e.json"

export ANKI_PIPELINE_LLM_FIXTURE_PATH=$DOCS/llm_mock_e2e.json
```

Re-run the `record.py` command if you add PDFs to the source-set YAML or change pipeline settings (`skip_lines_filter`, chunk size, etc.).

**Option B — live Bedrock:** unset the fixture and ensure AWS credentials are in `.env`:

```bash
unset ANKI_PIPELINE_LLM_FIXTURE_PATH
```

**Option C — CI-sized input only (Step 1 smoke):** use `$REPO/tests/baselines/inputs/sample.pdf` with `$REPO/tests/baselines/llm_mock.json`.

**Fresh state DB** (optional — use when you want a clean slate):

```bash
export ANKI_PIPELINE_STATE_DB_PATH=$DOCS/anki-pipeline-e2e-fresh.db
$CLI state init --db-path "$ANKI_PIPELINE_STATE_DB_PATH"
```

**Inspect state** (any step):

```bash
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" "SELECT COUNT(*) AS cards FROM cards;"
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" "SELECT run_id, trigger, started_at FROM runs ORDER BY started_at DESC LIMIT 5;"
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" "SELECT channel_id, source_set_name, page_token, expiration FROM drive_channels;"
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" "SELECT source_set_name, file_id, ready_at, force_process FROM pending_edits;"
$CLI state list-cards --db-path "$ANKI_PIPELINE_STATE_DB_PATH" | head
$CLI state list-runs --db-path "$ANKI_PIPELINE_STATE_DB_PATH"
```

---

## Step 0 — Automated gate (CI parity)

**Goal:** Catch regressions before manual work.

```bash
cd "$REPO"
.venv/bin/pytest
```

**Pass:** All tests green (currently 220+).

**Optional script-mode baseline** (same as CI):

```bash
export ANKI_PIPELINE_LLM_FIXTURE_PATH=$REPO/tests/baselines/llm_mock.json
.venv/bin/pytest tests/test_script_mode_baseline.py -v
```

---

## Step 1 — CLI one-shot `run` ✓

**Goal:** Original upload path still works; produces a standalone CSV without state.

**Status:** Verified — output at `~/Documents/e2e-one-shot.csv`.

**Prerequisites:** Personal LLM fixture (shared env block above). Reinstall the package if `--llm-fixture-path` is rejected on `run`:

```bash
cd "$REPO" && .venv/bin/pip install -e ".[dev]"
```

**Run:**

```bash
$CLI run "$DOCS/mandarin-notes-sample-1.pdf" \
  --output "$DOCS/e2e-one-shot.csv" \
  --cedict-path "$ANKI_PIPELINE_CEDICT_PATH" \
  --llm-fixture-path "$DOCS/llm_mock_e2e.json" \
  --disable-sentences
```

(`ANKI_PIPELINE_LLM_FIXTURE_PATH` alone also works if the flag is omitted.)

**Verify:**

```bash
test -s "$DOCS/e2e-one-shot.csv"
head -3 "$DOCS/e2e-one-shot.csv"
```

**Pass:** Non-empty CSV with vocabulary rows; exit code 0. With the E2E fixture, expect stub card `苹果` — that confirms the pipeline path, not real extraction quality.

**Next:** Step 2 (optional) or Step 3 if using `anki-pipeline-test.db` as-is.

---

## Step 2 — State init (fresh DB only)

**Goal:** Schema includes `cards`, `runs`, `drive_channels`, `pending_edits`.

Skip if using existing `anki-pipeline-test.db` (recommended after Step 1 — proceed to Step 3).

```bash
$CLI state init --db-path "$ANKI_PIPELINE_STATE_DB_PATH"
```

**Verify:**

```bash
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" ".tables"
```

**Pass:** Tables listed include `pending_edits` and `drive_channels`.

---

## Step 3 — Scheduled incremental sync (`demo` source set)

**Goal:** YAML exporters, document-level skip on re-run, CSV + XLSX output.

**Config:** `source_sets.demo` in `anki-pipeline-test-state.yaml`.

### 3a — Dry run (no LLM, no writes)

```bash
$CLI schedule \
  --source-set demo \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH" \
  --source-set-config "$ANKI_PIPELINE_SOURCE_SET_CONFIG" \
  --cedict-path "$ANKI_PIPELINE_CEDICT_PATH" \
  --llm-fixture-path "$ANKI_PIPELINE_LLM_FIXTURE_PATH" \
  --dry-run -v
```

**Pass:** Prints plan for `mandarin-notes-sample-1.pdf`; no new run row with finished LLM work.

### 3b — First real run

```bash
$CLI schedule \
  --source-set demo \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH" \
  --source-set-config "$ANKI_PIPELINE_SOURCE_SET_CONFIG" \
  --cedict-path "$ANKI_PIPELINE_CEDICT_PATH" \
  --llm-fixture-path "$DOCS/llm_mock_e2e.json" \
  -v
```

**Expected output (fixture mode):** The command prints a JSON report on stdout. With `llm_mock_e2e.json` this is **not** full PDF vocabulary — it is one stub card per processed document:

| Field | Expected (fixture) |
|-------|-------------------|
| `stats.sources_processed` | `1` |
| `stats.documents_skipped` | `0` |
| `outcomes[0].cards_created` | `1` |
| `demo-export.csv` data rows | **1 row** (`苹果` / apple) |

That single row confirms schedule → LLM → state → export works. It is normal for the CSV to look “empty” compared to a live Bedrock run.

**If JSON shows `documents_skipped: 1` and `sources_processed: 0`:** the PDF was already ingested on this DB (you are effectively at Step 3c). Use a fresh DB or delete the source row:

```bash
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" \
  "DELETE FROM sources WHERE external_id LIKE '%mandarin-notes-sample-1.pdf%';"
```

**Restore the pre-E2E database** (86 real cards from an earlier Bedrock run):

```bash
cp "$DOCS/anki-pipeline-test-old.db" "$DOCS/anki-pipeline-test.db"
export ANKI_PIPELINE_STATE_DB_PATH=$DOCS/anki-pipeline-test.db
```

**Verify:**

```bash
wc -l "$DOCS/demo-export.csv"          # fixture: 2 lines (header + 1 row)
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" "SELECT COUNT(*) FROM cards;"
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" \
  "SELECT trigger, json_extract(sync_report_json,'$.stats.sources_processed') FROM runs ORDER BY started_at DESC LIMIT 1;"
```

**Pass:** `trigger=schedule`; `sources_processed=1` on first run; export files exist; at least one card in state.

**For real vocabulary from your PDF:** unset the fixture and use Bedrock (`unset ANKI_PIPELINE_LLM_FIXTURE_PATH`), or restore `anki-pipeline-test-old.db` and re-export without re-ingesting.

### 3c — Second run (skip unchanged document)

```bash
$CLI schedule \
  --source-set demo \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH" \
  --source-set-config "$ANKI_PIPELINE_SOURCE_SET_CONFIG" \
  --cedict-path "$ANKI_PIPELINE_CEDICT_PATH" \
  --llm-fixture-path "$ANKI_PIPELINE_LLM_FIXTURE_PATH" \
  -v
```

**Pass:** Log/output shows document skipped by revision/hash; `documents_skipped` ≥ 1 in latest run report.

### 3d — Second source (`demo-second`)

**Config:** `source_sets.demo-second`.

```bash
$CLI schedule \
  --source-set demo-second \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH" \
  --source-set-config "$ANKI_PIPELINE_SOURCE_SET_CONFIG" \
  --cedict-path "$ANKI_PIPELINE_CEDICT_PATH" \
  --llm-fixture-path "$ANKI_PIPELINE_LLM_FIXTURE_PATH" \
  -v
```

**Verify:** `$DOCS/demo-second-export.csv` created.

---

## Step 4 — HTTP upload API (`serve`)

**Goal:** Web upload persists cards with `trigger=api-upload`; run history API works.

**Config:** Same env as above; server reads `ANKI_PIPELINE_STATE_*`.

**Terminal A — start server:**

```bash
export ANKI_PIPELINE_STATE_BACKEND=sqlite
export ANKI_PIPELINE_STATE_DB_PATH=$DOCS/anki-pipeline-test.db
export ANKI_PIPELINE_CEDICT_PATH=$REPO/tests/baselines/cedict_sample.u8
export ANKI_PIPELINE_LLM_FIXTURE_PATH=$DOCS/llm_mock_e2e.json

$CLI serve --port 8000 -v
```

**Terminal B — upload and inspect:**

```bash
curl -s http://127.0.0.1:8000/health | jq .

curl -s -X POST http://127.0.0.1:8000/api/sync/run \
  -F "file=@$DOCS/mandarin-notes-sample-3.pdf" \
  -F "skip_lines_filter=false" \
  -F "enable_sentences=false" | jq .

# Copy run_id from response, then:
RUN_ID="<paste-run-id>"
curl -s "http://127.0.0.1:8000/api/sync/runs/$RUN_ID" | jq '.trigger, .sync_report.stats'
```

**Pass:** `trigger` is `"api-upload"`; `cards_created` ≥ 1; health returns `ok`.

---

## Step 5 — Event-driven Drive (M8)

**Goal:** Watch channel → webhook → Mode A (`PendingEdits`) → Mode B → `trigger=drive-push`.

**Config:** Edit `demo-drive-fast` in `anki-pipeline-test-state.yaml`:

1. Replace `REPLACE_WITH_DRIVE_FOLDER_ID` with your Drive folder ID.
2. Ensure OAuth token exists:

```bash
$CLI auth google-drive --client-secrets /path/to/client_secret.json
# default token: ~/.config/anki-notes-pipeline/google-drive-token.json
```

### 5a — Simulated webhook (no ngrok; fastest local path)

Register a real channel (needs HTTPS URL once; for simulate-only you can use a placeholder URL if register accepts it, or skip register and insert a test channel — prefer full register below).

**Register watch** (requires public HTTPS — use ngrok in 5b, or register once with ngrok then use simulate locally):

```bash
$CLI drive watch register \
  --source-set demo-drive-fast \
  --webhook-url "https://YOUR-NGROK-ID.ngrok-free.app/api/drive/notifications" \
  --credentials-file ~/.config/anki-notes-pipeline/google-drive-token.json \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH"
```

Save the printed `channel_id`.

**Simulate change notification** (runs Mode A inline in CLI):

```bash
$CLI drive webhook simulate \
  --channel-id "<channel-id>" \
  --state change \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH"
```

**Verify pending row:**

```bash
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" \
  "SELECT source_set_name, file_id, ready_at, force_process FROM pending_edits;"
```

**Force immediate Mode B** (skip quiet window during testing):

```bash
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" \
  "UPDATE pending_edits SET force_process=1, ready_at=datetime('now');"
```

**Run Mode B:**

```bash
$CLI drive process-pending \
  --source-set demo-drive-fast \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH" \
  --source-set-config "$ANKI_PIPELINE_SOURCE_SET_CONFIG" \
  --cedict-path "$ANKI_PIPELINE_CEDICT_PATH" \
  --llm-fixture-path "$ANKI_PIPELINE_LLM_FIXTURE_PATH" \
  -v
```

**Verify:**

```bash
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" \
  "SELECT trigger FROM runs ORDER BY started_at DESC LIMIT 1;"
test -s "$DOCS/demo-drive-fast-export.csv"
```

**Pass:** Latest run has `trigger=drive-push`; export updated; pending row cleared (or preserved if edits arrived mid-run).

**Duplicate notification idempotency:**

```bash
$CLI drive webhook simulate --channel-id "<channel-id>" --state change \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH"
$CLI drive webhook simulate --channel-id "<channel-id>" --state change \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH"
sqlite3 "$ANKI_PIPELINE_STATE_DB_PATH" \
  "SELECT COUNT(*) FROM pending_edits WHERE source_set_name='demo-drive-fast';"
```

**Pass:** One logical pending row per `(source_set, file_id)`; no duplicate cards for the same vocabulary key.

### 5b — Live Google webhook (ngrok + `serve`)

**Terminal A — ngrok:**

```bash
ngrok http 8000
# note HTTPS URL, e.g. https://abc123.ngrok-free.app
```

**Terminal B — server** (same `ANKI_PIPELINE_STATE_*` env as Step 4):

```bash
$CLI serve --port 8000 -v
```

**Terminal C — register and edit:**

```bash
$CLI drive watch register \
  --source-set demo-drive-fast \
  --webhook-url "https://abc123.ngrok-free.app/api/drive/notifications" \
  --credentials-file ~/.config/anki-notes-pipeline/google-drive-token.json \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH"
```

1. Edit a Google Doc in the watched folder.
2. Wait for `quiet_minutes` (1 min for `demo-drive-fast`) or force pending edits (SQL above).
3. Run `drive process-pending` as in 5a.

**Pass:** Server logs show webhook POST; `pending_edits` populated; Mode B produces `drive-push` run.

**Unregister when done:**

```bash
$CLI drive watch unregister \
  --channel-id "<channel-id>" \
  --credentials-file ~/.config/anki-notes-pipeline/google-drive-token.json \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH"
```

### 5c — Channel renewal

```bash
$CLI drive watch renew \
  --webhook-url "https://abc123.ngrok-free.app/api/drive/notifications" \
  --credentials-file ~/.config/anki-notes-pipeline/google-drive-token.json \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH"
```

**Pass:** Prints renewed channel IDs or “No channels needed renewal.”

---

## Step 6 — AnkiWeb desktop agent (optional)

**Goal:** Cards from API upload or schedule appear in agent pending queue.

See also [`ankiweb-agent.md`](ankiweb-agent.md).

**Terminal A — server with agent secret:**

```bash
export ANKI_SERVER_AGENT_REGISTER_SECRET=e2e-test-secret
export ANKI_PIPELINE_STATE_BACKEND=sqlite
export ANKI_PIPELINE_STATE_DB_PATH=$DOCS/anki-pipeline-test.db
$CLI serve --port 8000
```

**Terminal B — register agent and poll:**

```bash
curl -s -X POST http://127.0.0.1:8000/api/ankiweb/agent/register \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"e2e-desktop","register_secret":"e2e-test-secret"}' | jq .

TOKEN="<paste-token>"

curl -s "http://127.0.0.1:8000/api/ankiweb/pending?agent_id=e2e-desktop" \
  -H "Authorization: Bearer $TOKEN" | jq '.items | length'
```

**Pass:** After Step 3 or 4 created cards, pending count ≥ 1.

Full desktop loop (Anki + AnkiConnect running): `$CLI agent setup --server-url http://127.0.0.1:8000 ...`

---

## Step 7 — Final smoke checklist

Run after all steps or before a release candidate:

| # | Journey | Command / check | Expected |
|---|---------|-----------------|----------|
| 1 | One-shot CLI | Step 1 | CSV exists ✓ |
| 2 | Schedule + skip | Steps 3b–3c | Second run skips unchanged doc |
| 3 | Exporters | `demo-export.csv`, `.xlsx` | Non-empty; XLSX has `Vocabulary` + `Run metadata` sheets |
| 4 | API upload | Step 4 | `trigger=api-upload` |
| 5 | Drive push | Step 5 | `trigger=drive-push` |
| 6 | State coherence | `sqlite3 ... "SELECT COUNT(*) FROM cards"` | Monotonic; no obvious duplicates |
| 7 | Agent queue | Step 6 (optional) | Pending items after sync |

**Full automated regression before sign-off:**

```bash
cd "$REPO" && .venv/bin/pytest && echo "E2E automated gate OK"
```

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| `schedule` can’t find source set | `echo $ANKI_PIPELINE_SOURCE_SET_CONFIG`; YAML `source_sets` key matches `--source-set` |
| Empty exports | Run finished without error; `runs.export_paths` in sync report JSON |
| Webhook 403 | `X-Goog-Channel-Token` must match stored `channel_token` |
| Pending never clears | `ready_at` in future — wait `quiet_minutes` or `force_process=1` in SQLite |
| Mode B no-op | Pending not ready; wrong `--source-set`; missing `--source-set-config` |
| Fixture CSV looks empty (1 row) | Expected with `llm_mock_e2e.json`; use Bedrock or restore `anki-pipeline-test-old.db` for real cards |
| Bedrock errors / fixture missing chunk | Run the personal fixture block above; or `unset ANKI_PIPELINE_LLM_FIXTURE_PATH` for live LLM |
| `sources_processed: 0`, `documents_skipped: 1` on “first” run | PDF already in state on this DB — fresh DB or delete `sources` row (see Step 3b) |
| `anki-pipeline-test.db` is 0 bytes | Re-run schedule or `cp anki-pipeline-test-old.db anki-pipeline-test.db` |
| Drive register fails | HTTPS URL required; domain verification for production (ngrok OK for dev) |
