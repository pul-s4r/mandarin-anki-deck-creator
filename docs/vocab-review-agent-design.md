# Vocabulary Review & Learning Prompt Agent — Design Strategy

## Status: Draft (iteration 1)

**Dependency**: This design assumes `anki-pipeline-core` has been extracted (see [core-library-extraction.md](./core-library-extraction.md)). The review agent depends on core for term data, tags, LLM access, and configuration.

---

## Purpose

An agent that takes vocabulary terms (organised by topic/lesson tags from the categorisation feature) and:

1. **Reminds** the user to do vocabulary reviews
2. **Organises** vocabulary reviews separately from Anki cards
3. **Generates** practice content (example sentences, dialogue prompts) for contextual learning

---

## User persona

A self-directed Mandarin learner who:
- Takes notes in classes (the source material for extraction)
- Uses Anki for SRS but wants **additional, contextual practice** beyond flashcard recall
- Has vocabulary organised by lesson date and topic (from the categorisation feature)
- Wants gentle nudges to review, not a rigid schedule they'll abandon

---

## Feature 1: Review Reminders

### Core problem

Anki's built-in SRS handles spaced repetition for individual cards. But the user also wants **topic-level** and **lesson-level** review prompts — "you haven't practised your Housing vocabulary in 10 days" rather than card-by-card scheduling.

### UX approaches

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

### Recommendation for iteration 1

Start with **Option A (daily digest)** delivered to stdout (CLI) with an optional webhook for messaging apps. It's the simplest to implement, doesn't require third-party calendar integrations, and gives the richest information at a glance. Add Option B as an enhancement once the scheduling engine is stable.

---

## Feature 2: Organised Review Sessions (separate from Anki)

### Core problem

Anki reviews are card-level, mixed across all topics, optimised for retention. The user also wants **coherent, topic-focused review sessions** — "spend 10 minutes on Housing vocabulary" — with progress tracked independently of Anki's SRS state.

### UX approaches

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

### Recommendation for iteration 1

Start with **Option A (CLI interactive session)** — it's the natural fit for a developer-user, provides per-term confidence data for scheduling, and is implementable without UI frameworks. Adopt adaptive elements from Option C incrementally (e.g., skip "easy" terms after 3 consecutive easy ratings).

---

## Feature 3: Practice Content Generation

### Core problem

Recognising a term in isolation (Anki) is different from using it correctly in context. The user wants the agent to **generate contextualised practice material** — example sentences and dialogue prompts — that exercises terms in realistic situations.

### UX approaches

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

### Recommendation for iteration 1

Start with **Option A (sentence drills)** as the foundation — it covers the full receptive-to-productive spectrum and each drill is atomic (easy to generate, store, and serve one at a time). Add **Option B (dialogue simulation)** as a second mode once the content generation pipeline is proven, since dialogues are the natural extension when the user is comfortable with individual sentence production.

Option C (reading passages) is a good "bonus" format that can be generated in batch (during the extraction/categorisation run) and served without real-time LLM calls.

---

## Review agent state model

The review agent owns its own local SQLite database (separate from the generator's state) containing:

| Table | Purpose |
|-------|---------|
| `review_sessions` | Completed and in-progress sessions (topic, start/end time, terms covered) |
| `review_schedule` | Per-topic scheduling state (last reviewed, interval, next due) |
| `term_confidence` | Per-term confidence history (rating, timestamp, session_id) |
| `generated_content` | Cached LLM-generated drills/passages (term_id, type, content, generated_at) |
| `notification_log` | When reminders were sent, which were actioned |

This state is:
- **Local-first**: works offline, sub-ms reads during interactive sessions
- **Independent**: a bug here never corrupts the generator's canonical card data
- **Eventually portable**: if multi-device becomes a requirement, this schema could move to DynamoDB with its own table (independent of the generator's migration)

---

## Summary of Recommendations

| Feature | Recommended approach | Rationale |
|---------|---------------------|-----------|
| Reminders | Daily digest (CLI + optional webhook) | Lowest friction, richest info, no third-party deps |
| Review sessions | CLI interactive with confidence rating | Natural for developer-user, feeds scheduling data |
| Content generation | Sentence drills (recognition → fill-blank → production) | Atomic, graduated, generates well from LLM |

---

## Iteration sequence

1. **Scaffold `vocab-review-agent`** — depends on core, empty CLI with `review` subcommand
2. **Implement scheduling engine** — per-topic interval tracking, "due" calculation
3. **Implement daily digest** (Feature 1) — reads schedule + term/tag state, outputs to terminal
4. **Implement interactive review session** (Feature 2) — CLI loop with confidence capture
5. **Implement sentence drill generation** (Feature 3) — LLM generates drills per-term, cached locally
6. **Connect scheduling ↔ sessions** — completing a session updates schedule state
7. **Add adaptive session behaviour** — skip easy terms, surface struggling terms
8. **Add dialogue mode** — real-time LLM conversation using topic terms

---

## Open questions for next iteration

1. **Notification delivery**: Is CLI stdout sufficient for reminders, or do we need Telegram/Discord/email from day one?
2. **Multi-device**: Will the user only review from their development machine, or also mobile? (This affects whether a CLI-only approach is viable long-term, and whether review state should eventually move to DynamoDB.)
3. **Confidence model**: Simple interval-based (like SM-2) or something lighter? How much should it overlap/conflict with Anki's own SRS state?
4. **Content caching**: Pre-generate drills during extraction (batch, cheaper) or generate on-demand during sessions (fresher, more adaptive)?
5. **Relationship to Anki**: Should review sessions mark terms as "reviewed" in a way that Anki can see (e.g., tagging), or are the two systems intentionally independent?
6. **Session length**: Fixed (always all terms in a topic) or time-boxed ("10 minutes of Housing")? Time-boxed is more realistic but needs term prioritisation logic.
