# Design: Vocabulary Categorization Feature

## Problem Statement

Terms extracted from lesson notes currently lack organizational metadata. Users need
to organise vocabulary by:

1. **Lesson date** — first-level grouping derived from date headers in the source
   document (e.g. `06/06`, `30/05`).
2. **Topic / theme** — second-level grouping (e.g. "Work culture", "Housing &
   Rent", "Job applications"). Some labels already appear explicitly in the notes
   (e.g. `996 Video Voc`); the rest must be inferred or supplied.

---

## Observations from the Source Notes

| Pattern | Example | Frequency |
|---------|---------|-----------|
| Date header (DD/MM or DD/MM- N) | `06/06- 1`, `30/05` | Every lesson boundary |
| Explicit topic label | `996 Video Voc` | Occasional |
| Numbered vocabulary list | `1. 简历 jian3 li4` | Common within lessons |
| Tab-separated vocabulary block | `休闲时间 \t xiūxián shíjiān \t leisure time` | Common |
| Dialogues section | `Dialogues:\nA: 房东说下个月…` | End of some lessons |

The current preprocessing step `optional_drop_metadata_lines` **discards** these
date headers. Categorization requires **preserving and interpreting** them as section
boundaries.

---

## Data Model Extension

### `VocabularyRow` / `LlmVocabularyItem`

```python
# New fields
lesson_date: str = ""       # ISO date or raw label, e.g. "2024-06-06"
topic: str = ""             # e.g. "Work culture / 996"
topic_source: str = ""      # "explicit" | "inferred" | "user"
```

### CSV export (new columns)

```
Key, Simplified, …, LessonDate, Topic
```

These map directly to **Anki tags** or **deck hierarchies** at import time:
`Mandarin::2024-06-06::Work_Culture`.

### SQLite `cards` table

Add nullable columns `lesson_date TEXT`, `topic TEXT`, `topic_source TEXT`.
Migration via `schema_version` bump.

---

## Implementation: Lesson Date (Level 1)

This layer is largely **deterministic** — no LLM required.

### Strategy

1. During preprocessing, **detect section boundaries** before chunking:
   - Regex: `^\s*(\d{1,2}[/\-]\d{1,2})(?:\s*-\s*\d+)?\s*$`
   - Parse to partial date; infer year from document context or filename.
2. Build a `SectionMap`: ordered list of `(line_number, lesson_date)` pairs.
3. After LLM extraction, each `LlmVocabularyItem` carries a `source_offset`
   (character or line position in the normalized text). Look up the enclosing
   section to assign `lesson_date`.

### Alternative: chunk-level provenance

Each chunk already has a deterministic position in the document. If chunks are
small enough that they don't span lesson boundaries, the section map lookup can
happen per-chunk rather than per-term. This is simpler but less precise for large
chunks that cross date boundaries.

### Integration point

A new `preprocess/sections.py` module:

```python
@dataclass
class Section:
    start_line: int
    lesson_date: str        # normalised partial date string
    raw_header: str         # original line text
    explicit_topic: str     # e.g. "996 Video Voc" if present, else ""

def detect_sections(text: str) -> list[Section]: ...
```

Called early in the pipeline (after `normalize_unicode`, **before**
`optional_drop_metadata_lines` — or replacing that filter entirely for
categorization-aware runs).

---

## Implementation: Topic / Theme (Level 2)

### Strategies for Providing Group Labels

#### Strategy A — Fully User-Specified

| Aspect | Detail |
|--------|--------|
| Mechanism | YAML/JSON sidecar mapping `lesson_date → topic` |
| Pros | Full control, no LLM cost, deterministic |
| Cons | Manual effort, doesn't scale, labels may lag |
| When to use | Small corpus, user already maintains notes structure |

Example config:

```yaml
topics:
  "2024-06-06":
    topic: "Housing & Rent / Job Applications"
  "2024-05-30":
    topic: "Shopping & Value / Work-life Balance"
  "996 Video Voc":
    topic: "Work Culture / 996 / Lying Flat"
```

#### Strategy B — Agent-Inferred, User-Reviewed

| Aspect | Detail |
|--------|--------|
| Mechanism | LLM pass over each section's terms → proposed topic label |
| Pros | Low user effort, scales, leverages context |
| Cons | LLM cost, may produce inconsistent labels across runs |
| When to use | Growing corpus, user wants suggestions |

Implementation sketch:

1. After extraction, group terms by `lesson_date`.
2. For each group, send a lightweight prompt:
   > Given these Mandarin vocabulary terms from a single lesson: [list].
   > Suggest 1–3 concise topic labels in English (e.g. "Work culture",
   > "Daily routines"). If the section has an explicit title, use it.
3. Store proposed labels with `topic_source = "inferred"`.
4. Present to user for approval / edit (CLI interactive mode, or exported
   review file).

#### Strategy C — Hybrid (Recommended)

| Aspect | Detail |
|--------|--------|
| Mechanism | Extract explicit labels from notes → LLM fills gaps → user confirms |
| Pros | Respects existing structure, minimises LLM calls, user retains control |
| Cons | Slightly more complex pipeline |
| When to use | Default mode for most users |

Flow:

```
┌─────────────────┐
│ Detect sections  │──→ explicit_topic from header?
└────────┬────────┘           │
         │                   YES → use as-is (topic_source="explicit")
         │ NO
         ▼
┌─────────────────┐
│ LLM topic pass   │──→ proposed_topic (topic_source="inferred")
└────────┬────────┘
         ▼
┌─────────────────┐
│ User review      │──→ confirmed_topic (topic_source="user")
└─────────────────┘
```

User review interface options (increasing complexity):

1. **Review file** — pipeline writes `topics_review.yaml` with proposed labels;
   user edits and re-runs. Simplest, fits CLI workflow.
2. **Interactive CLI** — prompt user during run for sections needing labels.
3. **Web UI** — separate application (see architecture section below).

#### Strategy D — Taxonomy-Seeded

For users who want consistent labels across many lessons:

1. Define a fixed taxonomy (YAML list of allowed topic labels).
2. LLM classifies each section against the taxonomy (constrained output).
3. New labels require explicit taxonomy extension by user.

Benefit: prevents label drift ("Work culture" vs "Working life" vs "996 culture").

---

## User Feedback Loop Mechanics

Regardless of strategy chosen, a **feedback persistence layer** is needed:

```python
@dataclass
class TopicAssignment:
    lesson_date: str
    topic: str
    source: Literal["explicit", "inferred", "user"]
    confidence: float       # 0.0–1.0 for inferred labels
    user_confirmed: bool
    last_updated: datetime
```

Stored in:
- The existing SQLite state DB (new `topic_assignments` table), OR
- A sidecar YAML file (simpler, version-controllable, portable).

On subsequent pipeline runs, confirmed assignments are re-used without re-inferring.
Only new/unconfirmed sections trigger the LLM or prompt the user.

---

## Architecture: When to Break Out a Separate Application

### Keep Integrated (Recommended for Now)

The categorization feature should remain **inside `anki-deck-generator`** when:

- The interaction model is **batch/CLI** (review files, non-interactive inference).
- There is no interactive UI beyond terminal prompts.
- The feature shares the same data lifecycle (ingest → extract → enrich → **categorize** → export).
- You want a single `pip install` and unified configuration.

Specifically, adding `preprocess/sections.py`, a `categorize/` module, and
extending `VocabularyRow` + CSV export is a natural extension of the existing
pipeline stages.

### Break Out When…

| Signal | Why it warrants separation |
|--------|--------------------------|
| **Interactive review UI** (web-based drag & drop, label editing) | Different runtime (web server), different deployment, different dependencies (React/Flask/etc.) |
| **Multi-user feedback** (team shares taxonomy, reviews each other's labels) | Needs auth, persistent server, API layer |
| **Label management becomes its own domain** (taxonomy CRUD, hierarchy editing, cross-document consistency enforcement) | Separate bounded context with its own persistence and business rules |
| **Reuse across other language pipelines** (Japanese, Korean, etc.) | Generic categorization engine not specific to Mandarin/Anki |
| **Different release cadence** — categorization evolves faster or independently of extraction | Coupling slows both down |

### Proposed Architecture if Split

```
┌─────────────────────────┐     ┌──────────────────────────┐
│ anki-deck-generator     │     │ vocab-categorizer        │
│ (extraction pipeline)   │     │ (topic assignment)       │
│                         │     │                          │
│ • Ingest                │     │ • Section detection      │
│ • LLM extraction        │────▶│ • Topic inference (LLM)  │
│ • CEDICT enrichment     │◀────│ • User feedback loop     │
│ • Sentence linking      │     │ • Taxonomy management    │
│ • CSV/Anki export       │     │ • Review UI (web)        │
└─────────────────────────┘     └──────────────────────────┘
         │                                │
         ▼                                ▼
   ┌──────────┐                    ┌──────────────┐
   │ state.db │                    │ taxonomy.db  │
   └──────────┘                    └──────────────┘
```

### Common Parts to Abstract

If breaking out, these components become **shared libraries**:

| Component | Current location | Shared concern |
|-----------|-----------------|----------------|
| `VocabularyRow` / data model | `dictionary/enrich.py` | Both apps produce/consume vocabulary rows |
| `Section` / section detection | New `preprocess/sections.py` | Both need to parse document structure |
| `StateStore` protocol | `state/store.py` | Persistence interface (SQLite, future Postgres) |
| `CardRecord` | `state/records.py` | Shared identity / dedup key (`user_id + simplified`) |
| LLM client abstraction | `llm/bedrock_chain.py` | Both call LLMs; share model config, retry, schema validation |
| Text normalization | `preprocess/normalize.py` | Shared preprocessing |
| Ingest routing | `ingest/router.py` | Both need PDF/MD/DOCX → text |

Extraction pattern: publish a **`anki-pipeline-core`** package containing the data
model, ingest, normalization, and state interfaces. Both applications depend on it.

### Recommendation

**Phase 1** — Keep integrated. Add section detection + lesson_date tagging
(deterministic). Add LLM topic inference as an optional pipeline stage. Use review
file for user feedback. No new application.

**Phase 2** — If interactive review becomes a requirement, build a minimal
**FastAPI** service with a lightweight frontend (or TUI) that reads/writes the same
SQLite state DB and topic assignments sidecar. The extraction pipeline remains CLI.

**Phase 3** — If the topic management domain grows complex (taxonomy versioning,
multi-user, cross-corpus consistency), extract shared code into
`anki-pipeline-core` and treat categorization as a sibling application.

---

## Summary of Recommendations

| Decision | Recommendation |
|----------|---------------|
| Lesson date extraction | Deterministic section detection (no LLM needed) |
| Topic labelling strategy | **Hybrid (Strategy C)**: explicit → inferred → user review |
| User feedback mechanism | Start with review YAML file; upgrade to TUI/web later |
| Architecture | Keep integrated for now; split only when interactive UI is needed |
| Shared abstractions | Data model, ingest, normalization, state protocol, LLM client |
| Taxonomy consistency | Optional Strategy D overlay for users with large corpora |

---

## Next Steps (Implementation Order)

1. `preprocess/sections.py` — section boundary + date detection
2. Extend `VocabularyRow`, `LlmVocabularyItem`, CSV schema with `lesson_date`, `topic`
3. Wire section map into pipeline (assign `lesson_date` per term)
4. Add optional LLM topic inference pass (Strategy B/C)
5. Implement review file read/write (YAML sidecar)
6. SQLite schema migration for `lesson_date` + `topic` on `cards`
7. Anki tag generation from categorization fields during export
