# Progress log

Newest entries first. See `~/AgentWS/code-standards/docs/harness/state-tracking.md`.

---

## 2026-06-27 — Harness initialized

**Status**: complete  
**Goal**: Bootstrap project harness (AGENTS.md, architecture doc, state files)

**Delivered**:

- AGENTS.md and docs/architecture.md created
- Environment profile: Python package, CLI, and API
- CI gate config: `.agent/ci-gate.json` (enabled with `pytest -q`)

**Commands discovered**:

- Install: `pip install -e ".[dev,sync,ankiweb,server]"`
- Run (one-shot): `anki-notes-pipeline run /path/to/notes.pdf --output out.csv`
- Run (incremental): `anki-notes-pipeline schedule --source-set myset --state-db ... --source-set-config sources.yaml`
- Test: `pytest -q`
- Lint: `ruff check src/ tests/ && ruff format --check src/ tests/`
- Build: `pip wheel . -w dist/`

**Next increment**: Run `plan` for first feature, then `plan-state-sync` to seed `feature_list.json`

**Git**: created on current branch
