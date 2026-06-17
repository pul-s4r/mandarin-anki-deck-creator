# Milestone 6 — AnkiWeb agent loop

## Prerequisites

The `agent` subcommand is available on the **`m6-ankiweb-agent`** branch. If you see:

```text
invalid choice: 'agent' (choose from 'run', 'state', 'schedule', 'auth', 'import', 'serve')
```

you are on an older branch or using a stale install. Check out `m6-ankiweb-agent` and reinstall:

```bash
git checkout m6-ankiweb-agent
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,server,ankiweb]"
```

Confirm the right CLI is on your `PATH`:

```bash
.venv/bin/anki-notes-pipeline --help
# must list: run, state, schedule, auth, import, serve, agent
```

Use `.venv/bin/anki-notes-pipeline` for every command below (or `pip install -e` into your active venv from the repo root).

---

## Manual verification

### 1. Install extras

```bash
.venv/bin/pip install -e ".[dev,server,ankiweb]"
```

### 2. Configure environment

Use one shared SQLite file for **both** the server and any CLI persistence commands:

```bash
export ANKI_PIPELINE_STATE_BACKEND=sqlite
export ANKI_PIPELINE_STATE_DB_PATH=/tmp/anki-state.db
export ANKI_SERVER_AGENT_REGISTER_SECRET=dev-secret
export ANKI_PIPELINE_CEDICT_PATH="$(pwd)/tests/baselines/cedict_sample.u8"
```

Optional: point Bedrock at a fixture for deterministic runs (no AWS):

```bash
export ANKI_PIPELINE_LLM_FIXTURE_PATH="$(pwd)/tests/baselines/llm_mock.json"
```

### 3. Start the server

Terminal A:

```bash
.venv/bin/anki-notes-pipeline serve --host 127.0.0.1 --port 8000
```

Smoke check:

```bash
curl -s http://127.0.0.1:8000/health
```

### 4. Register the desktop agent

Terminal B (same env vars as step 2):

```bash
.venv/bin/anki-notes-pipeline agent setup \
  --server-url http://127.0.0.1:8000 \
  --agent-id desktop \
  --register-secret dev-secret
```

This writes `~/.config/anki-notes-pipeline/agent.toml`, copies the pull-agent script, creates an isolated venv, and installs a login service (systemd user / launchd / Task Scheduler).

**Without the CLI** (same server must be running):

```bash
curl -s -X POST http://127.0.0.1:8000/api/ankiweb/agent/register \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"desktop","register_secret":"dev-secret"}'
```

Save the returned `token` into `agent.toml` manually if you skip `agent setup`.

Check local agent status:

```bash
.venv/bin/anki-notes-pipeline agent status
```

### 5. Populate cards (persistence path)

The pull agent only syncs cards already stored in `StateStore` (`CardRecord` rows where `ankiweb_last_synced_at` is missing or older than `last_updated_at`).

**Important:** `POST /api/sync/run` runs the pipeline and returns vocabulary rows in the HTTP response, but it does **not** write cards into `StateStore` today. For agent testing you must persist cards through one of the paths below.

#### Path A — CLI schedule (recommended)

Uses the same incremental-sync stack as production and writes cards + a run report to `ANKI_PIPELINE_STATE_DB_PATH`.

1. Create a tiny source-set config:

```bash
cat >/tmp/agent-test-sources.yaml <<'EOF'
source_sets:
  agent-test:
    sources:
      - provider: local-filesystem
        paths:
          - tests/baselines/inputs/sample.md
EOF
export ANKI_PIPELINE_SOURCE_SET_CONFIG=/tmp/agent-test-sources.yaml
```

2. Run schedule (same `ANKI_PIPELINE_STATE_DB_PATH` as the server):

```bash
.venv/bin/anki-notes-pipeline schedule \
  --source-set agent-test \
  --state-db "$ANKI_PIPELINE_STATE_DB_PATH" \
  --output /tmp/agent-test.csv \
  --cedict-path "$ANKI_PIPELINE_CEDICT_PATH" \
  --llm-fixture-path "$ANKI_PIPELINE_LLM_FIXTURE_PATH"
```

3. Confirm cards landed in state:

```bash
.venv/bin/anki-notes-pipeline state list-cards --db-path "$ANKI_PIPELINE_STATE_DB_PATH"
.venv/bin/anki-notes-pipeline state list-runs --db-path "$ANKI_PIPELINE_STATE_DB_PATH"
```

Note the latest `run_id` from `list-runs` for step 7.

#### Path B — API upload (pipeline only, no persistence yet)

Useful to verify upload + LLM pipeline over HTTP; **not sufficient alone** for the agent loop until cards are persisted via Path A.

```bash
curl -s -F "file=@tests/baselines/inputs/sample.md" \
  http://127.0.0.1:8000/api/sync/run | jq '.stats'
```

After this, `GET /api/ankiweb/pending` will still return an empty batch until you run Path A (or otherwise upsert `CardRecord` rows).

#### Path C — Quick manual seed (agent smoke test only)

Insert one pending card directly (skips the pipeline):

```bash
.venv/bin/python <<'PY'
from datetime import UTC, datetime
from pathlib import Path
import os
from anki_deck_generator.state.sqlite_store import SqliteStateStore
from anki_deck_generator.state.records import CardRecord

db = Path(os.environ["ANKI_PIPELINE_STATE_DB_PATH"])
store = SqliteStateStore(db)
store.init_schema()
now = datetime.now(UTC)
store.upsert_card(CardRecord(
    card_id="seed-c1",
    simplified="的",
    meaning="possessive particle",
    last_updated_at=now,
    first_seen_source_id="manual-seed",
))
store.close()
print("seeded 1 card into", db)
PY
```

### 6. Agent applies cards to Anki

1. Install [AnkiConnect](https://ankiweb.net/shared/info/2055492159) in desktop Anki and keep Anki running.
2. Ensure the login service from step 4 is active (`agent status` shows a cursor / no stuck inflight batch).
3. Within ~60s the agent should `GET /api/ankiweb/pending`, apply notes via AnkiConnect, and `POST /api/ankiweb/ack`.
4. In Anki, browse for notes tagged `ext_id:<card_id>`.

### 7. Observability

If you used Path A, inspect the run report (replace `RUN_ID`):

```bash
curl -s http://127.0.0.1:8000/api/sync/runs/RUN_ID | jq '.exports_ankiweb'
```

After the agent acks, that block should show `created` / `updated` counts and `sync_status`.

---

## Automated tests

```bash
.venv/bin/pip install -e ".[dev,server,ankiweb,google-drive]"
.venv/bin/pytest tests/test_ankiweb_agent_service.py tests/test_ankiweb_web_api.py tests/test_agent_loop.py -v
.venv/bin/pytest -q
```
