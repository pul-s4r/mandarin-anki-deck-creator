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

## DynamoDB table design

Both packages write to the **same DynamoDB instance** but with clear table ownership. Three tables in the shared instance, one table per app for app-specific data.

### Table overview

| Table | Owner | Generator | Review Agent |
|-------|-------|-----------|-------------|
| `vocab_cards` | Generator | Read/Write | Read-only |
| `vocab_tags` | Core | Read/Write | Read/Write |
| `generator_sync` | Generator | Read/Write | No access |
| `review_state` | Review Agent | No access | Read/Write |

### `vocab_cards` — canonical term data

Owned by the generator. The review agent reads from this to know what terms exist and what their definitions are.

```
Table: vocab_cards
Partition key: user_id (String)
Sort key:      term_id (String, UUID)

Attributes:
├── simplified        (String)     "房租"
├── traditional       (String)     "房租"
├── pinyin            (String)     "fángzū"
├── meaning           (String)     "rent (for housing)"
├── part_of_speech    (String)     "noun"
├── usage_notes       (String)     "Often used with 交 (pay) or 涨 (increase)"
├── content_hash      (String)     SHA-256 of semantic fields
├── first_seen_source (String)     source_id that introduced this term
├── created_at        (String)     ISO-8601
├── updated_at        (String)     ISO-8601
└── ankiweb_note_id   (Number)     [generator-specific, ignored by review agent]

GSI-1: cards_by_simplified
  Partition key: user_id
  Sort key:      simplified
  Purpose:       Deduplication lookup during extraction

GSI-2: cards_by_updated
  Partition key: user_id
  Sort key:      updated_at
  Purpose:       "Give me all cards changed since last sync"
```

**Example item:**
```json
{
  "user_id": "default",
  "term_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "simplified": "房租",
  "traditional": "房租",
  "pinyin": "fángzū",
  "meaning": "rent (for housing)",
  "part_of_speech": "noun",
  "usage_notes": "Often used with 交 (pay) or 涨 (increase)",
  "content_hash": "8f14e45f...",
  "first_seen_source": "src_abc123",
  "created_at": "2026-03-15T10:00:00Z",
  "updated_at": "2026-05-01T14:30:00Z",
  "ankiweb_note_id": 1698234567
}
```

### `vocab_tags` — shared categorisation data

Owned by core. Both apps read and write. The generator writes tags during extraction (lesson dates from note headers, topics from LLM inference). The review agent writes tags when the user confirms or adds labels during review sessions.

```
Table: vocab_tags
Partition key: user_id (String)
Sort key:      tag_id (String, UUID)

Attributes:
├── term_id       (String)     FK → vocab_cards.term_id
├── dimension     (String)     "lesson_date" | "topic" | "custom"
├── value         (String)     "2026-05-15" or "Housing & Rent" or user-defined
├── source        (String)     "explicit" | "inferred" | "user"
├── confirmed     (Boolean)    true/false
├── created_at    (String)     ISO-8601
├── created_by    (String)     "generator" | "review-agent" | "user"
└── updated_at    (String)     ISO-8601

GSI-1: tags_by_term
  Partition key: user_id#term_id (String, composite)
  Sort key:      dimension#value (String, composite)
  Purpose:       "Get all tags for a specific term"

GSI-2: tags_by_dimension_value
  Partition key: user_id#dimension (String, composite)
  Sort key:      value
  Purpose:       "Get all terms tagged with topic 'Housing & Rent'"
                 This is the critical query for the review agent —
                 "give me all terms in this topic for a review session"
```

**Example items:**
```json
[
  {
    "user_id": "default",
    "tag_id": "tag-001",
    "term_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "dimension": "lesson_date",
    "value": "2026-05-15",
    "source": "explicit",
    "confirmed": true,
    "created_at": "2026-05-15T10:00:00Z",
    "created_by": "generator",
    "updated_at": "2026-05-15T10:00:00Z"
  },
  {
    "user_id": "default",
    "tag_id": "tag-002",
    "term_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "dimension": "topic",
    "value": "Housing & Rent",
    "source": "inferred",
    "confirmed": false,
    "created_at": "2026-05-15T10:00:00Z",
    "created_by": "generator",
    "updated_at": "2026-05-15T10:00:00Z"
  },
  {
    "user_id": "default",
    "tag_id": "tag-003",
    "term_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "dimension": "topic",
    "value": "Housing & Rent",
    "source": "user",
    "confirmed": true,
    "created_at": "2026-05-19T12:00:00Z",
    "created_by": "review-agent",
    "updated_at": "2026-05-19T12:00:00Z"
  }
]
```

Note: item 3 shows the review agent confirming the inferred tag — in practice this would update item 2 (set `confirmed: true`, `source: "user"`, `updated_at`) rather than create a duplicate. Shown separately for clarity.

### `generator_sync` — generator-only extraction state

Owned exclusively by the generator. The review agent never touches this.

```
Table: generator_sync
Partition key: user_id (String)
Sort key:      entity_key (String, composite: "type#id")

Entity types stored here (discriminated by sort key prefix):
├── SOURCE#<source_id>     → provider, external_id, content_sha256, revision_id, etag
├── CHUNK#<source_id>#<idx> → chunk_sha256, model_id, llm_output_card_ids[]
├── RUN#<run_id>           → trigger, started_at, finished_at, report_json
└── CHANNEL#<channel_id>   → resource_id, page_token, expiration (Drive push)
```

Single-table design here makes sense because these entities are only ever queried by the generator, always scoped to a user, and the access patterns are simple (get by key, list by prefix).

### `review_state` — review agent-only state

Owned exclusively by the review agent. The generator never touches this. **This table only exists if the review agent opts into DynamoDB for its state** (e.g., for multi-device sync). In Phase 1, this data lives in local SQLite instead.

```
Table: review_state
Partition key: user_id (String)
Sort key:      entity_key (String, composite: "type#id")

Entity types:
├── SCHEDULE#<topic_hash>       → topic, last_reviewed_at, interval_days, next_due
├── SESSION#<session_id>        → topic, started_at, ended_at, terms_covered, terms_total
├── CONFIDENCE#<term_id>#<ts>   → rating (1-4), session_id
├── CONTENT#<term_id>#<type>    → drill_type, generated_text, generated_at, model_id
└── NOTIFICATION#<notif_id>     → sent_at, channel, topics_included, actioned
```

### Access patterns by app

| Query | Table | Used by | How |
|-------|-------|---------|-----|
| Get term by simplified (dedup) | `vocab_cards` | Generator | GSI-1: `user_id` + `simplified` |
| Get all terms (export) | `vocab_cards` | Generator | Query PK = `user_id` |
| Get terms changed since timestamp | `vocab_cards` | Review Agent | GSI-2: `user_id` + `updated_at > X` |
| Get all tags for a term | `vocab_tags` | Both | GSI-1: `user_id#term_id` |
| Get all terms for a topic | `vocab_tags` | Review Agent | GSI-2: `user_id#topic` + value = "Housing & Rent" → returns `term_id`s → batch-get from `vocab_cards` |
| Get all topics (list) | `vocab_tags` | Review Agent | GSI-2: `user_id#topic`, scan sort keys for distinct values |
| Write a new tag | `vocab_tags` | Both | PutItem |
| Confirm a tag | `vocab_tags` | Review Agent | UpdateItem (set `confirmed=true`) |
| Record review session | `review_state` | Review Agent | PutItem |
| Get schedule for all topics | `review_state` | Review Agent | Query PK=`user_id`, SK begins_with `SCHEDULE#` |

### Consistency model

#### `vocab_cards` — eventually consistent reads are sufficient

| Property | Detail |
|----------|--------|
| Writer | Generator only (single writer) |
| Reader | Review agent (read-only) |
| Read mode | Eventually consistent (default, half-cost RCUs) |
| Staleness risk | Generator runs as a batch job (daily/weekly). Review agent reads at session start. These workflows don't overlap in time. |

The review agent does **not** require strongly consistent reads from `vocab_cards`. The argument:

1. **Temporal separation**: The generator finishes extraction → writes cards → exits. Minutes/hours/days later, the review agent loads terms for a session. By that point, eventual consistency has long since converged (DynamoDB typically propagates within milliseconds; worst case is single-digit seconds).

2. **Idempotent operation**: If the review agent loads a slightly stale term list (missing 1-2 terms from a just-completed extraction run), the consequence is: those terms don't appear in *this* session. They appear in the next one. No duplicates are created because:
   - Sessions are keyed by `term_id` + `session_id` (timestamp)
   - Schedule state tracks topics, not individual term-session pairs
   - The digest/reminder calculates "due" based on last-reviewed timestamps — a missed term doesn't corrupt the schedule

3. **No read-after-write dependency across apps**: The review agent never writes to `vocab_cards` and then reads its own write. It only reads what the generator wrote at some earlier point.

**Bottom line**: Eventually consistent reads. No transactions needed. The review agent is tolerant of being one extraction run behind.

#### `vocab_tags` — application-level consistency (no DynamoDB transactions needed)

| Property | Detail |
|----------|--------|
| Writers | Generator (during extraction) + Review agent (during user confirmation) |
| Conflict type | Semantic, not temporal — "generator re-infers what user already corrected" |
| Resolution | `confirmed` flag acts as a write guard |

**Why DynamoDB-level consistency mechanisms (transactions, conditional writes) are largely unnecessary:**

1. **No concurrent writes in practice**: Generator runs as a batch job. Review agent runs interactively. Single user. These don't execute simultaneously. There's no real race condition on the same item.

2. **The actual risk is semantic, not temporal**: The generator might re-run extraction on updated notes and re-infer a topic tag for a term that the user already confirmed or corrected. This isn't a DynamoDB consistency problem — it's a "who wins?" business rule.

**Resolution rule — `confirmed` flag as write guard:**

```
Generator write logic for tags:
  1. Check: does a tag with this (term_id, dimension, value) already exist?
  2. If yes AND confirmed == true → skip (user has spoken, don't overwrite)
  3. If yes AND confirmed == false → update (re-inference, maybe new value)
  4. If no → create (new tag, source="inferred", confirmed=false)
```

This can be implemented as a conditional write for safety:

```
UpdateItem:
  Key: { user_id, tag_id }
  ConditionExpression: "confirmed <> :true"
  UpdateExpression: "SET #value = :new_value, source = :src, updated_at = :now"
```

If the condition fails (tag was already confirmed), the generator silently skips. No error, no conflict — the user's decision is authoritative.

**Review agent write logic:**
- Confirming a tag: `UpdateItem SET confirmed=true, source="user", updated_at=now` — no condition needed (confirming is always safe, regardless of current state)
- Correcting a tag value: `UpdateItem SET value=:new, confirmed=true, source="user"` — user correction is always authoritative
- Adding a new tag: `PutItem` — no conflict possible (new UUID, new item)

**Summary**: One conditional write on the generator side (don't clobber confirmed tags). Everything else is unconditional. No transactions, no locking, no read-modify-write cycles.

#### `review_state` — no consistency concerns

Single writer (review agent), single reader (review agent). No shared access. Standard DynamoDB writes are sufficient. Strongly consistent reads can be used here cheaply since it's low-volume and the review agent is the only consumer.

#### Does the review agent need the latest `vocab_cards` to operate correctly?

**No.** The review agent is idempotent and tolerant of lag.

| Scenario | Consequence | Recovery |
|----------|-------------|----------|
| Generator adds 3 new terms to "Housing" after last review session | Terms don't appear in current session | They appear in the next session or next digest |
| Generator updates a term's meaning after review agent cached it | Review agent shows slightly stale meaning during session | Next session loads fresh data; no state corruption |
| Generator deletes/replaces a term | Review agent might show a term that no longer exists in cards | Session still completes; term drops out of future sessions when next tag query doesn't resolve it |
| Generator adds a new topic | Topic doesn't appear in this run's digest | Appears in next digest/schedule calculation |

The review agent's contract is: **"at session start, load whatever terms exist for this topic right now."** If the data is one extraction run behind, the user simply reviews what was available. Next run picks up the delta. No double-creation of reviews because:
- Sessions are uniquely identified (UUID + timestamp)
- Schedule advances are keyed by topic + last_reviewed_at (not by which specific terms were reviewed)
- Confidence ratings are per-(term_id, timestamp) — duplicate term_ids in different sessions are fine, they just add more data points

This means the review agent can even **cache term data locally** (in its SQLite) and refresh periodically rather than hitting DynamoDB on every session start — useful for offline mode.

### Cost and throughput considerations

- **`vocab_cards`**: Low write volume (batch extraction runs, maybe daily/weekly). Read volume from review agent is also low (load N terms at session start). Eventually consistent reads = half the RCU cost.
- **`vocab_tags`**: Low volume both directions. Tag creation happens during extraction; tag confirmation happens during review. Neither is high-frequency. One conditional write per tag during extraction (cheap).
- **On-demand capacity** is appropriate for all tables — no need for provisioned throughput at single-user scale.
- **Total cost at single-user scale**: effectively free tier (< 25 WCU/RCU sustained, < 25GB storage).

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

1. ~~**Monorepo vs multi-repo**~~ → Resolved: monorepo, multiple packages (Option B).
2. **Versioning**: Does core follow its own semver, or is it versioned in lockstep with the generator?
3. ~~**DynamoDB table design**~~ → Resolved: hybrid approach. Separate tables for shared data (`vocab_cards`, `vocab_tags`) with GSIs for cross-app query patterns. Single-table design for app-specific state (`generator_sync`, `review_state`) where access patterns are simpler.
4. **Generator backwards compatibility**: During extraction, does the generator keep a copy of `VocabularyRow` locally (thin adapter over `core.models.VocabularyTerm`) or fully adopt the core model? Adapter is lower-risk.
5. ~~**Tag conflict resolution**~~ → Resolved: `confirmed` flag as write guard. Generator uses conditional writes to skip confirmed tags. User-confirmed always wins. No transactions needed.
