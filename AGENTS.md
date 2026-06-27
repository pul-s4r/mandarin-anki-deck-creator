# AGENTS.md - Guide for AI Agents

Map-style entrypoint for this project. Keep this file concise (~80-120 lines). Put depth in [docs/architecture.md](docs/architecture.md).

## Project overview

**mandarin-anki-deck-creator** — Convert Chinese study notes (PDF/Markdown/DOCX/Google Drive) to Anki vocabulary cards via Amazon Bedrock LLM + CC-CEDICT enrichment.

- **Owner**: jjmoey
- **License**: Unknown
- **Primary language**: Python 3.12+
- **Environment profile**: Python package, CLI, and API (see `~/AgentWS/code-standards/docs/harness/environment-conventions.md`)

## Quick start commands

Run from project root unless noted otherwise.

```bash
# Install dependencies
pip install -e ".[dev,sync,ankiweb,server]"

# Run locally (one-shot)
anki-notes-pipeline run /path/to/notes.pdf --output out.csv --cedict-path /path/to/cedict_ts.u8

# Incremental sync with state
anki-notes-pipeline schedule --source-set myset --state-db ~/.local/share/anki-notes-pipeline/state.db --source-set-config sources.yaml

# Lint / format
ruff check src/ tests/ && ruff format --check src/ tests/

# Type check
# No type gate configured — project uses Pydantic models but no mypy/pyright in CI.

# Test
pytest -q

# Build
pip wheel . -w dist/
```

## Verification before done

| Gate | Command / surface |
|------|-------------------|
| CI gate | `pytest -q` (see `.agent/ci-gate.json`, `.cursor/hooks/review-config.json`) |
| Runtime (CLI) | `anki-notes-pipeline run` exits 0 + produces CSV; error cases exit 1 with `error:` on stderr |
| Runtime (API) | `anki-notes-pipeline serve` → `GET /health`, `POST /api/sync/run` (optional `[server]` extra) |

See `~/AgentWS/code-standards/docs/harness/runtime-verification.md` for verdict rules (`PASS`, `FAIL`, `BLOCKED`, `SKIP`).

Final runtime verification: run the **`verify`** skill after all dev increments are committed.

## Harness integration

| Artifact | Path |
|----------|------|
| Architecture | [docs/architecture.md](docs/architecture.md) |
| State ledger | `feature_list.json` |
| Progress log | `PROGRESS.md` |
| Decision log | `docs/decisions/` |
| CI gate config | `.agent/ci-gate.json` (`.cursor/hooks/review-config.json` on Cursor) |

Fresh-session routine:

1. Read this file and `docs/architecture.md`.
2. Read newest entries in `PROGRESS.md`.
3. Read `feature_list.json` and any linked `docs/decisions/` records with status `accepted`.
4. Pick the smallest unblocked increment; run verification gates before declaring done.

## Hard constraints

- Python >= 3.12 required; CI runs on 3.12 only.
- LLM calls go through Amazon Bedrock (boto3 + langchain-aws); use `--llm-fixture-path` or `ANKI_PIPELINE_LLM_FIXTURE_PATH` for deterministic tests.
- State DB is SQLite (local) or DynamoDB (cloud/Epic F); core `run_incremental_sync` must work unchanged across both.
- Project-specific instructions override global `code-standards` rules when documented here intentionally.

## Precedence

1. This file and project-local docs
2. `.cursor/rules/` in this repo (if present)
3. Global rules synced from `~/AgentWS/code-standards/rules/`

## Deeper docs

- [Architecture](docs/architecture.md)
- [Change detection](docs/change-detection.md)
- [Harness catalog](~/AgentWS/code-standards/docs/harness/README.md)
