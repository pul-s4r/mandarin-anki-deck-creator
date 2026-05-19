# Vocabulary Review Agent — Design Strategy

## Status: Draft (iteration 1)

---

## Part 1: Component Split — `anki-pipeline-core`

### Rationale

The vocabulary review agent needs access to the same data model, normalisation logic, state protocol, and LLM client that `anki-deck-generator` uses. Building it as a second consumer of those abstractions is the trigger for extracting shared code into a standalone package.

### What moves into `anki-pipeline-core`

| Component | Current location | Why shared |
|-----------|-----------------|------------|
| Data model | `dictionary/enrich.py` (`VocabularyRow`), `state/records.py` (`CardRecord`, etc.) | Both apps need the canonical term representation |
| Normalisation | `preprocess/normalize.py`, `preprocess/fingerprints.py` | Consistent text handling across ingest and review content generation |
| State protocol | `state/store.py` (`StateStore`), `state/sqlite_store.py` | Review agent needs read/write access to the same term database |
| LLM client abstraction | `llm/bedrock_chain.py` (extraction interface), `llm/fixture_player.py` | Review agent uses LLM for content generation; same provider config |
| Categorisation (new) | `categorize/` (tag store protocol, models) | Tags drive review scheduling and content selection |
| Config base | `config/settings.py` (AWS/Bedrock settings subset) | Shared credentials and model configuration |

### What stays in `anki-deck-generator`

- Ingest layer (PDF/DOCX/Markdown parsing)
- Preprocessing beyond normalisation (chunking, table detection, section splitting)
- CC-CEDICT dictionary and enrichment
- Export formats (CSV, Anki-specific output)
- Google Drive integration
- The `run` / `schedule` / `import` CLI handlers

### What lives in the new review agent package

- Review scheduling engine
- Practice content generation (sentences, dialogues)
- Notification/reminder delivery
- Its own CLI surface and eventual UI
- Review session state (separate from extraction state)

### Package topology

```
anki-pipeline-core/          ← shared library (no CLI)
├── models/                  ← VocabularyTerm, Tag, CardRecord
├── state/                   ← StateStore protocol + SQLite impl
├── normalise/               ← unicode, fingerprints
├── llm/                     ← LLM client protocol + Bedrock impl + fixture stub
├── categorise/              ← TagStore protocol, tag models
└── config/                  ← shared settings (AWS, model ID, DB path)

anki-deck-generator/         ← extraction pipeline (depends on core)
├── ingest/
├── preprocess/
├── dictionary/
├── export/
├── sync/
└── cli/

vocab-review-agent/          ← new package (depends on core)
├── scheduling/
├── content/
├── notifications/
├── sessions/
└── cli/
```

### Migration approach

1. Extract `anki-pipeline-core` as a separate package within a monorepo (or adjacent repo) with its own `pyproject.toml`
2. `anki-deck-generator` depends on `anki-pipeline-core` — initially as a path dependency during development
3. `vocab-review-agent` depends on `anki-pipeline-core`
4. Both apps share a single SQLite state database (same `StateStore` protocol, different tables for app-specific state)
5. No breaking changes to the existing CLI; the extraction pipeline continues to work identically

### Shared database strategy

The SQLite database gains new tables for the review agent but keeps the existing schema intact:

- **Existing tables** (owned by extraction pipeline): `sources`, `chunks`, `cards`, `runs`, `drive_channels`
- **New shared tables** (owned by core): `term_tags`, `tag_dimensions`
- **New review tables** (owned by review agent): `review_sessions`, `review_schedule`, `generated_content`, `notification_log`

Both apps open the same DB file. Schema migrations are versioned per-owner (core, extraction, review) to allow independent deployment.

---

## Part 2: Vocabulary Review Agent — UX Strategy

### User persona

A self-directed Mandarin learner who:
- Takes notes in classes (the source material for extraction)
- Uses Anki for SRS but wants **additional, contextual practice** beyond flashcard recall
- Has vocabulary organised by lesson date and topic (from the categorisation feature)
- Wants gentle nudges to review, not a rigid schedule they'll abandon

---

### Feature 1: Review Reminders

#### Core problem
Anki's built-in SRS handles spaced repetition for individual cards. But the user also wants **topic-level** and **lesson-level** review prompts — "you haven't practised your Housing vocabulary in 10 days" rather than card-by-card scheduling.

#### UX approaches (choose one)

**Option A: Digest-style daily summary**

The agent generates a short daily digest (delivered via email, messaging app, or terminal on login) that says:

> *3 topics are due for review today:*
> - **Work Culture** (12 terms, last practised 5 days ago)
> - **Housing & Rent** (8 terms, last practised 8 days ago)
> - **Directions** (6 terms, last practised 12 days ago — overdue)
>
> *Run `vocab review start` to begin, or `vocab review pick housing` to focus on one.*

Characteristics:
- Passive — user reads it, decides whether to act
- Low friction — doesn't interrupt workflow
- Batched — one notification per day with all due items
- Escalation — overdue items get increasingly prominent (bold, moved to top, eventually flagged)

**Option B: Contextual nudge at shell login**

A lightweight prompt hook (shell RC integration or terminal multiplexer status line) that shows the single most overdue topic:

> `[vocab] "Housing & Rent" — 8 terms, 12 days since last review. Run: vocab review start housing`

Characteristics:
- Zero-effort to consume — visible without opening an app
- Single item — doesn't overwhelm
- Dismissable — disappears after the next review or after a configurable snooze

**Option C: Calendar-integrated scheduling**

The agent creates calendar events (via CalDAV or Google Calendar API) for review sessions, with topic and estimated duration:

> *Review: Housing & Rent (8 terms, ~5 min)*
> *Thursday 7:00 PM*

Characteristics:
- Integrated into existing time management
- Explicit time commitment — user sees it alongside other obligations
- Higher friction to set up, but leverages existing reminder infrastructure
- Risk: becomes "just another calendar event to dismiss"

#### Recommendation for iteration 1

Start with **Option A (daily digest)** delivered to stdout (CLI) with an optional webhook for messaging apps. It's the simplest to implement, doesn't require third-party calendar integrations, and gives the richest information at a glance. Add Option B as an enhancement once the scheduling engine is stable.

---

### Feature 2: Organised Review Sessions (separate from Anki)

#### Core problem
Anki reviews are card-level, mixed across all topics, optimised for retention. The user also wants **coherent, topic-focused review sessions** — "spend 10 minutes on Housing vocabulary" — with progress tracked independently of Anki's SRS state.

#### UX approaches (choose one)

**Option A: CLI-driven interactive session**

```
$ vocab review start --topic "Housing & Rent"

Session: Housing & Rent (8 terms)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Term 1/8: 房租 (fángzū)

  What does this mean?
  > [user types or presses Enter to reveal]

  Meaning: rent (for housing)
  
  How confident? [1-easy  2-ok  3-hard  4-forgot]: _
```

Characteristics:
- Familiar flashcard-like interaction but topic-scoped
- Confidence self-rating feeds back into scheduling (hard terms resurface sooner)
- Progress is per-session: "you reviewed 6/8 terms in Housing today"
- Can be interrupted and resumed
- Offline — no network needed during the session itself

**Option B: Generated review document**

The agent produces a formatted review document (Markdown or PDF) per topic that the user works through at their own pace:

```markdown
# Review: Housing & Rent
## Generated: 2026-05-19

### Terms to recall (cover the right column)
| Simplified | Pinyin | Meaning |
|---|---|---|
| 房租 | fángzū | ??? |
| 押金 | yājīn | ??? |
...

### Self-check answers
| Simplified | Meaning |
|---|---|
| 房租 | rent |
| 押金 | security deposit |
...
```

Characteristics:
- Printable / works on any device (e-reader, tablet, paper)
- No interactive tooling required during the session
- User marks completion manually (or the CLI asks "did you finish?" afterwards)
- Less granular feedback — no per-term confidence rating unless user annotates

**Option C: Session with adaptive difficulty**

Like Option A, but the agent adjusts mid-session based on performance:

- If user marks several terms "easy" → skip remaining easy ones, surface harder terms or move to production (see Feature 3)
- If user struggles → slow down, offer hints (first character, example sentence), reduce session scope
- Session ends adaptively: "You've mastered 5/8 today, 3 remaining for next time"

Characteristics:
- More engaging — feels responsive
- Requires tracking per-term difficulty history
- More complex to implement (state machine per session)
- Risk: over-engineering for a single user

#### Recommendation for iteration 1

Start with **Option A (CLI interactive session)** — it's the natural fit for a developer-user, provides per-term confidence data for scheduling, and is implementable without UI frameworks. Adopt adaptive elements from Option C incrementally (e.g., skip "easy" terms after 3 consecutive easy ratings).

---

### Feature 3: Practice Content Generation

#### Core problem
Recognising a term in isolation (Anki) is different from using it correctly in context. The user wants the agent to **generate contextualised practice material** — example sentences and dialogue prompts — that exercises terms in realistic situations.

#### UX approaches (choose one)

**Option A: Sentence drills (receptive → productive)**

A graduated sequence per term or topic:

1. **Recognition**: Show a sentence with the target term, ask for the meaning
   > 他每个月的**房租**是三千块。→ What does 房租 mean here?

2. **Fill-in-the-blank**: Show the sentence with the term removed
   > 他每个月的 _____ 是三千块。(hint: relates to housing cost)

3. **Production**: Give an English prompt, user produces the Chinese sentence
   > "Express that your rent increased this month."

Characteristics:
- Clear skill progression (understand → recall → produce)
- Generated by LLM with the term and topic as context
- Each drill is self-contained — can be done in 30 seconds
- LLM can vary sentence difficulty based on user's current level

**Option B: Dialogue simulation**

The agent generates a dialogue scenario and the user plays one role:

```
Scenario: You're asking your landlord about the lease terms.

Landlord: 你好，你是来看房的吧？
You: > [type your response using 房租, 押金, 合同]

[Agent evaluates response, offers corrections, continues dialogue]
```

Characteristics:
- Highly contextual — simulates real conversation
- Exercises multiple terms together within a topic
- Requires real-time LLM interaction (not pre-generated)
- More engaging but also more demanding (user needs productive ability)
- Natural fit for topic-scoped sessions ("practice a Housing conversation")

**Option C: Reading comprehension passages**

The agent generates a short paragraph (3-5 sentences) using several terms from the topic, followed by comprehension questions:

```
Passage:
小王刚搬到北京，他在网上找了一个月的房子。最后他找到了一个
离公司很近的公寓，房租每个月四千块，还要交两个月的押金...

Questions:
1. 小王找了多长时间的房子？
2. 房租是多少？
3. 押金要交几个月的？
```

Characteristics:
- Exercises reading comprehension with multiple terms in context
- Can be generated in advance (no real-time LLM needed during the session)
- Lower pressure than production tasks
- Good complement to sentence drills

#### Recommendation for iteration 1

Start with **Option A (sentence drills)** as the foundation — it covers the full receptive-to-productive spectrum and each drill is atomic (easy to generate, store, and serve one at a time). Add **Option B (dialogue simulation)** as a second mode once the content generation pipeline is proven, since dialogues are the natural extension when the user is comfortable with individual sentence production.

Option C (reading passages) is a good "bonus" format that can be generated in batch (during the extraction/categorisation run) and served without real-time LLM calls.

---

## Summary of Recommendations

| Feature | Recommended approach | Rationale |
|---------|---------------------|-----------|
| Reminders | Daily digest (CLI + optional webhook) | Lowest friction, richest info, no third-party deps |
| Review sessions | CLI interactive with confidence rating | Natural for developer-user, feeds scheduling data |
| Content generation | Sentence drills (recognition → fill-blank → production) | Atomic, graduated, generates well from LLM |

### Iteration sequence

1. **Extract `anki-pipeline-core`** — data model, state, normalisation, LLM client, config
2. **Scaffold `vocab-review-agent`** — depends on core, empty CLI with `review` subcommand
3. **Implement scheduling engine** — per-topic interval tracking, "due" calculation
4. **Implement daily digest** (Feature 1) — reads schedule state, outputs to terminal
5. **Implement interactive review session** (Feature 2) — CLI loop with confidence capture
6. **Implement sentence drill generation** (Feature 3) — LLM generates drills per-term, cached
7. **Connect scheduling ↔ sessions** — completing a session updates schedule state
8. **Add dialogue mode** — real-time LLM conversation using topic terms

---

## Open Questions for Next Iteration

1. **Notification delivery**: Is CLI stdout sufficient for reminders, or do we need Telegram/Discord/email from day one?
2. **Multi-device**: Will the user only review from their development machine, or also mobile? (This affects whether a CLI-only approach is viable long-term.)
3. **Confidence model**: Simple interval-based (like SM-2) or something lighter? How much should it overlap/conflict with Anki's own SRS state?
4. **Content caching**: Pre-generate drills during extraction (batch, cheaper) or generate on-demand during sessions (fresher, more adaptive)?
5. **Relationship to Anki**: Should review sessions mark terms as "reviewed" in a way that Anki can see (e.g., tagging), or are the two systems intentionally independent?
