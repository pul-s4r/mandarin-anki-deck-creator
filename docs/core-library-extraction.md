# Core Library Extraction — `anki-pipeline-core`

## Status: Draft (iteration 1)

---

## Rationale

The vocabulary review agent needs access to the same data model, normalisation logic, state protocol, and LLM client that `anki-deck-generator` uses. Building it as a second consumer of those abstractions is the trigger for extracting shared code into a standalone package.

---

## What moves into `anki-pipeline-core`

| Component | Current location | Why shared |
|-----------|-----------------|------------|
| Data model | `dictionary/enrich.py` (`VocabularyRow`), `state/records.py` (`CardRecord`, etc.) | Both apps need the canonical term representation |
| Normalisation | `preprocess/normalize.py`, `preprocess/fingerprints.py` | Consistent text handling across ingest and review content generation |
| State protocol | `state/store.py` (`StateStore`), `state/sqlite_store.py` | Review agent needs read/write access to the same term database |
| LLM client abstraction | `llm/bedrock_chain.py` (extraction interface), `llm/fixture_player.py` | Review agent uses LLM for content generation; same provider config |
| Categorisation (new) | `categorize/` (tag store protocol, models) | Tags drive review scheduling and content selection |
| Config base | `config/settings.py` (AWS/Bedrock settings subset) | Shared credentials and model configuration |

---

## What stays in `anki-deck-generator`

- Ingest layer (PDF/DOCX/Markdown parsing)
- Preprocessing beyond normalisation (chunking, table detection, section splitting)
- CC-CEDICT dictionary and enrichment
- Export formats (CSV, Anki-specific output)
- Google Drive integration
- The `run` / `schedule` / `import` CLI handlers

---

## What lives in downstream consumers (e.g. review agent)

- Application-specific business logic
- Application-specific CLI surface
- Application-specific local state (session progress, schedules, caches)

---

## Package topology — monorepo with multiple packages (Option B)

### Decision

Single repository, three packages, each with its own `pyproject.toml`. During development all three are editable-installed into one virtual environment. The separation exists at the packaging level (independent install, independent dependency declarations) not at the environment level.

### Repository layout

```
mandarin-vocab-tools/              ← monorepo root
├── packages/
│   ├── core/                      ← shared library (no CLI)
│   │   ├── pyproject.toml         ← name: anki-pipeline-core
│   │   └── src/anki_pipeline_core/
│   │       ├── models/            ← VocabularyTerm, Tag, CardRecord
│   │       ├── state/             ← StateStore protocol + SQLite impl + DynamoDB impl
│   │       ├── normalise/         ← unicode, fingerprints
│   │       ├── llm/              ← LLM client protocol + Bedrock impl + fixture stub
│   │       ├── categorise/        ← TagStore protocol, tag models
│   │       └── config/            ← shared settings (AWS, model ID, DB path)
│   │
│   ├── generator/                 ← extraction pipeline (depends on core)
│   │   ├── pyproject.toml         ← name: anki-deck-generator; deps: [anki-pipeline-core]
│   │   └── src/anki_deck_generator/
│   │       ├── ingest/
│   │       ├── preprocess/
│   │       ├── dictionary/
│   │       ├── export/
│   │       ├── sync/
│   │       └── cli/
│   │
│   └── review-agent/              ← review & learning agent (depends on core)
│       ├── pyproject.toml         ← name: vocab-review-agent; deps: [anki-pipeline-core]
│       └── src/vocab_review_agent/
│           ├── scheduling/
│           ├── content/
│           ├── notifications/
│           ├── sessions/
│           └── cli/
│
├── tests/                         ← shared test infrastructure (or per-package tests/)
├── pyproject.toml                 ← workspace root (optional: dev tooling config)
└── README.md
```

### Development environment

One venv, all packages editable-installed:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e "packages/core[dev]"
pip install -e "packages/generator[dev]"
pip install -e "packages/review-agent[dev]"
```

Changes to core are immediately visible to both apps without reinstalling. No separate environments needed day-to-day.

### Dependency declarations (per-package pyproject.toml)

| Package | Runtime deps | Path dep on core |
|---------|-------------|-----------------|
| `core` | pydantic, boto3, langchain-core | — |
| `generator` | pymupdf, python-docx, pyyaml | `anki-pipeline-core` (path: `../core`) |
| `review-agent` | (TBD — likely minimal beyond core) | `anki-pipeline-core` (path: `../core`) |

Path dependencies resolve locally during development. For distribution (if ever published), they'd become version-pinned PyPI deps.

### CI / testing

- Single GitHub Actions workflow that installs all three packages and runs `pytest` across the whole repo
- Can be split into per-package jobs later if test suites grow large (overkill for now)

### When environments ARE separate

Only when apps are deployed to different targets:
- Generator runs as a cron job / CI pipeline on a server → its own Docker image or venv with `pip install anki-deck-generator`
- Review agent runs on your laptop interactively → installed in your local dev venv
- But during development, both run from the same workspace

---

## Migration approach

1. Restructure the current repo into `packages/core` + `packages/generator` (move files, update imports)
2. Create `packages/review-agent` as a new package depending on core
3. All three share the monorepo — atomic commits across packages when core's contract changes
4. No breaking changes to the existing CLI; `anki-notes-pipeline` entry point stays in generator
5. Existing tests continue to pass after restructure (import paths update from `anki_deck_generator.state` → `anki_pipeline_core.state`, etc.)

---

## State ownership and the DynamoDB migration

### Context

The extraction pipeline is on a trajectory from SQLite (local, single-user) to DynamoDB (cloud, multi-device). Once that migration lands, SQLite is deprecated for card state in the generator. This raises the question: how do downstream consumers access shared data?

### Decision: separate state ownership, shared data contract

Downstream consumers should **not** share a single DynamoDB table or database instance directly with the generator. Instead:

| Concern | Extraction pipeline (generator) | Downstream consumers |
|---------|-------------------------------|--------------|
| Card state (canonical terms) | DynamoDB (primary, post-migration) | **Read-only** via core's `StateStore` protocol |
| Tags / categorisation | DynamoDB (shared tables, owned by core) | Read + write via core's `TagStore` protocol |
| App-specific state | N/A | **Own store** (local SQLite or own DynamoDB table) |
| Sync metadata (sources, chunks) | DynamoDB (owned by generator) | No access needed |

### Why not a single shared store for everything?

1. **Coupling**: If both apps write to the same tables, schema changes in one app risk breaking the other. The generator's card table has fields (`ankiweb_note_id`, `content_hash`, chunk linkage) irrelevant to consumers, and consumers have state (session progress, confidence ratings) that would pollute the generator's data model.

2. **Operational independence**: Consumers may need to work offline. If they depend on DynamoDB for their core loop, they can't function without network. The generator can afford to require network — it's a batch pipeline.

3. **Cost and latency**: Interactive apps need sub-100ms response. Reading a few terms from DynamoDB is fine; writing every interaction event there adds unnecessary latency and cost.

4. **Blast radius**: A bug in a consumer's write path shouldn't be able to corrupt the canonical card data that the generator owns.

### What IS shared via core

- **The `StateStore` protocol** gains a DynamoDB implementation (alongside SQLite). All apps use this protocol to read canonical term data.
- **The `TagStore` protocol** — tags live in core-owned tables, writable by any app (the generator assigns tags during extraction; consumers can confirm/add tags).
- **The data contract** (term schema, tag schema) is defined in core and versioned. All apps depend on core's models, not on each other's tables.

### Architecture diagram

```
┌─────────────────────────────────────────────────────┐
│                  DynamoDB (cloud)                    │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐                │
│  │ cards table  │  │  tags table  │                │
│  │ (generator   │  │  (core owns, │                │
│  │  owns)       │  │   all r/w)   │                │
│  └──────┬───────┘  └──────┬───────┘                │
│         │                  │                        │
└─────────┼──────────────────┼────────────────────────┘
          │                  │
          │  reads           │  reads + writes
          ▼                  ▼
┌──────────────────────────────────────┐
│         anki-pipeline-core           │
│  StateStore protocol (DynamoDB impl) │
│  TagStore protocol (DynamoDB impl)   │
└──────────┬───────────────┬───────────┘
           │               │
    ┌──────┘               └──────┐
    ▼                              ▼
┌────────────────┐      ┌──────────────────┐
│ anki-deck-     │      │ downstream       │
│ generator      │      │ consumers        │
│                │      │                  │
│ writes cards   │      │ reads cards/tags │
│ writes tags    │      │ writes tags      │
│                │      │ owns app state   │
└────────────────┘      └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐
                        │ Local SQLite     │
                        │ (app-specific)   │
                        │                  │
                        │ sessions         │
                        │ schedules        │
                        │ caches           │
                        └──────────────────┘
```

### Migration timeline

- **Phase 1 (now)**: All apps use SQLite via `StateStore` protocol. Consumers have their own DB file for app state; they read term/tag data from the generator's DB file (or a shared one).
- **Phase 2 (DynamoDB migration)**: The generator moves card state to DynamoDB. Core gains a `DynamoStateStore` implementation. Consumers switch their term/tag reads to the DynamoDB-backed protocol — **no code change in the consumer itself**, just a config change (`backend = "dynamodb"` instead of `"sqlite"`).
- **Phase 3 (SQLite deprecated for generator)**: The generator drops its local SQLite. Consumers **keep their own local SQLite** for app-specific, latency-sensitive state. Only shared reads go through DynamoDB.

---

## Extraction boundaries — detailed module mapping

### `core.models`

| Source | Destination | Changes |
|--------|------------|---------|
| `dictionary/enrich.py::VocabularyRow` | `core.models.VocabularyTerm` | Rename; remove `key` field (sequential key is export concern); add `term_id: str` (UUID) |
| `state/records.py::CardRecord` | `core.models.CardRecord` | Move as-is; drop `ankiweb_*` fields (generator-specific) |
| `state/records.py::compute_card_content_hash` | `core.models.content_hash` | Move as-is |
| New | `core.models.Tag` | `tag_id`, `term_id`, `dimension` (lesson_date/topic/custom), `value`, `source` (explicit/inferred/user), `confirmed: bool` |

### `core.state`

| Source | Destination | Changes |
|--------|------------|---------|
| `state/store.py::StateStore` | `core.state.CardStore` | Narrow to card read/write; drop sync-specific methods |
| `state/sqlite_store.py` (card portion) | `core.state.SqliteCardStore` | Extract card-only operations |
| New | `core.state.TagStore` | Protocol for tag CRUD |
| New | `core.state.SqliteTagStore` | SQLite tag implementation |
| New (Phase 2) | `core.state.DynamoCardStore` | DynamoDB card read |
| New (Phase 2) | `core.state.DynamoTagStore` | DynamoDB tag read/write |

### `core.normalise`

| Source | Destination | Changes |
|--------|------------|---------|
| `preprocess/normalize.py::normalize_unicode` | `core.normalise.unicode` | Move as-is |
| `preprocess/fingerprints.py` | `core.normalise.fingerprints` | Move as-is |

### `core.llm`

| Source | Destination | Changes |
|--------|------------|---------|
| `llm/bedrock_chain.py` (client setup, invoke) | `core.llm.bedrock` | Extract the Bedrock client factory; leave extraction-specific prompt/schema in generator |
| `llm/fixture_player.py` | `core.llm.fixture` | Move as-is (useful for testing any consumer) |
| New | `core.llm.protocol` | `LlmClient` protocol: `invoke(system_prompt, user_prompt) -> str` |

### `core.config`

| Source | Destination | Changes |
|--------|------------|---------|
| `config/settings.py` (AWS/Bedrock subset) | `core.config.LlmSettings` | AWS region, model ID, temperature, fixture path |
| `config/settings.py` (state subset) | `core.config.StateSettings` | Backend choice, DB path |

---

## Open questions

1. **Monorepo vs multi-repo**: Should core, generator, and review agent live in one repo (simpler CI, atomic cross-package changes) or separate repos (independent versioning, cleaner ownership)?
2. **Versioning**: Does core follow its own semver, or is it versioned in lockstep with the generator?
3. **DynamoDB table design**: Single-table design (PK=`user_id`, SK=`entity#id`) or separate tables per entity? Affects query patterns for "all terms for topic X".
4. **Generator backwards compatibility**: During extraction, does the generator keep a copy of `VocabularyRow` locally (thin adapter over `core.models.VocabularyTerm`) or fully adopt the core model? Adapter is lower-risk.
