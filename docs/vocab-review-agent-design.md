# Vocabulary Review & Learning Prompt Agent — Design Strategy

## Status: Draft (iteration 2)

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

## Feature 4: Cross-Device Access & Conversation Carryover

### Core problem

The CLI-based review agent (Features 1–3) works well on a laptop, but a self-directed learner doesn't always study from a terminal. Common scenarios:

- **Commuting**: 10 free minutes on the subway — phone is available, laptop is not
- **Between classes**: quick review on the phone right after the lesson that introduced the vocabulary
- **Evening wind-down**: review from the couch without opening a laptop
- **Mid-session switch**: start a dialogue drill on the laptop, continue it from the phone during lunch

The requirement is not "build a mobile app" — it's **make the review agent accessible from a phone and ensure sessions carry over when switching devices**.

### Architecture requirement: server-backed state

Cross-device access means two clients (CLI + phone interface) need consistent state. This has a hard prerequisite: **the review agent's SQLite database cannot remain purely local**. At minimum, session and schedule state must be accessible from both devices.

This aligns with the DynamoDB migration path in [core-library-extraction.md](./core-library-extraction.md) — the review agent's tables (`review_sessions`, `term_confidence`, `review_schedule`, `generated_content`, `notification_log`) move to DynamoDB (or a similar cloud store). The CLI keeps a local SQLite cache for offline sessions and syncs when connectivity returns.

### UX approaches for phone access

**Option A: Messaging bot (Telegram)**

The review agent exposes itself as a Telegram bot. The user interacts through the chat interface:

```
Bot: 📚 3 topics are due for review today:
     • Work Culture (12 terms, last practised 5 days ago)
     • Housing & Rent (8 terms, last practised 8 days ago)
     • Directions (6 terms, overdue — 12 days)

     Tap a topic to start, or /skip to snooze until tomorrow.

User: Housing & Rent

Bot: Session: Housing & Rent (8 terms)

     Term 1/8: 房租 (fángzū)
     What does this mean?

     [Reveal answer]

User: [taps Reveal]

Bot: Meaning: rent (for housing)

     How confident?
     [1-easy] [2-ok] [3-hard] [4-forgot]

User: [taps 2-ok]

Bot: Term 2/8: 押金 (yājīn)
     ...
```

Characteristics:
- **Zero install**: the user already has Telegram; no new app to download or maintain
- **Notifications built-in**: daily digest becomes a Telegram message — push notifications already work on iOS/Android
- **Conversation-native**: dialogue drills (Feature 3, Option B) map naturally to a chat interface
- **Inline keyboards**: confidence ratings, topic selection, and reveal buttons use Telegram's inline keyboard API — no typing required on mobile
- **Shareable**: a study partner could be added to the same bot for accountability
- **Limitations**: no rich formatting (tables, pinyin tone marks render inconsistently), media attachment needed for audio, Telegram API rate limits apply

**Option B: Lightweight web app (PWA)**

A minimal Progressive Web App served by the review agent's API server:

```
┌─────────────────────────────┐
│  Vocab Review          [≡]  │
│─────────────────────────────│
│                             │
│  Housing & Rent  (8 terms)  │
│                             │
│        房租                 │
│      fángzū                 │
│                             │
│    What does this mean?     │
│                             │
│      [ Reveal answer ]      │
│                             │
│─────────────────────────────│
│  2/8 reviewed today         │
│  Session started on laptop  │
│  at 2:15 PM — continuing    │
└─────────────────────────────┘
```

Characteristics:
- **Installable on home screen**: feels like a native app, launches full-screen
- **Full control over UX**: rich character rendering, proper pinyin display, custom layouts for drill types
- **Offline-capable**: service worker caches the current session's terms and generated content
- **More work to build**: requires a frontend (even if minimal), hosting, and HTTPS
- **Responsive**: works on phone, tablet, and desktop browser — could replace the CLI entirely for non-terminal users

**Option C: CLI + `tmux` / SSH (power-user workaround)**

The user SSH-es into their development machine from a mobile terminal app (Termux, Blink, Prompt) and resumes the CLI session.

Characteristics:
- **No new code**: works today with existing CLI design
- **Poor mobile UX**: terminal interaction on a phone keyboard is cumbersome
- **Network-dependent**: requires the laptop to be on, accessible, and running
- **Useful as a stopgap**, not as a long-term solution

### Recommendation for iteration 1

Start with **Option A (Telegram bot)** as the primary mobile interface:

1. **Lowest new-code cost**: the Telegram Bot API is a thin HTTP wrapper — `python-telegram-bot` or raw `httpx` calls. No frontend framework, no hosting of static assets, no HTTPS certificate management.
2. **Notifications for free**: the daily digest (Feature 1) becomes a Telegram message with push delivery. This replaces the webhook approach mentioned in Feature 1 with a concrete channel.
3. **Natural chat UX for dialogues**: when Feature 3 dialogue mode ships, the Telegram interface is already a conversation — there's no UX impedance mismatch.
4. **Session carryover works immediately**: both CLI and Telegram bot read/write the same review state store. Starting a session on the CLI and continuing on Telegram (or vice versa) requires only that both clients resolve session state from the same source.

Add **Option B (PWA)** as a follow-on if the user wants richer display (e.g., stroke-order animations, audio playback) or if the Telegram dependency becomes undesirable.

### Conversation carryover: how it works

"Carry over a conversation" means: **when you switch devices mid-session, the new device picks up exactly where you left off**, with full context of what's been reviewed, what confidence ratings were given, and where in a dialogue you are.

#### Session state model (extended for multi-device)

The `review_sessions` table gains fields for device tracking:

| Column | Type | Purpose |
|--------|------|---------|
| `session_id` | text PK | UUID per session |
| `topic` | text | Topic being reviewed |
| `status` | text | `active`, `paused`, `completed`, `abandoned` |
| `started_at` | timestamp | When the session began |
| `started_on` | text | Device/client that started the session (`cli`, `telegram`, `web`) |
| `last_active_at` | timestamp | Last interaction time (used for timeout/handoff detection) |
| `last_active_on` | text | Device/client of last interaction |
| `current_term_idx` | integer | Which term the user is currently on |
| `terms_order` | json | Ordered list of term_ids for this session (fixed at session start) |
| `completed_terms` | json | Map of term_id → confidence rating for terms already reviewed |

#### Carryover flow

```
Laptop (CLI)                          Phone (Telegram)
─────────────                         ────────────────
1. User starts session
   → session_id=abc, status=active,
     started_on=cli, current_term_idx=0

2. Reviews terms 1–4, rates each
   → current_term_idx=4,
     completed_terms={t1:2, t2:1, t3:3, t4:2}

3. User closes laptop (or just walks away)
   → last_active_at = 14:32
                                      4. User opens Telegram, taps "Continue session"
                                         → bot reads session abc,
                                           sees current_term_idx=4, status=active
                                         → "Continuing Housing & Rent — 4/8 done.
                                            Term 5/8: 合同 (hétong)"

                                      5. Reviews terms 5–8
                                         → current_term_idx=8, status=completed

6. User returns to laptop later
   → CLI shows "Session completed
     on your phone at 15:10.
     Next topic: Work Culture."
```

#### Conflict handling

Two devices should not be actively reviewing the same session simultaneously. The protocol:

1. When a client starts interacting with an active session, it checks `last_active_at`.
2. If `last_active_at` is within a **liveness window** (e.g., 2 minutes), the session is considered in-use on the other device. The client shows: *"This session is active on your [laptop]. Switch here? The other device will be paused."*
3. If `last_active_at` is older than the liveness window, the client assumes the other device is idle and takes over silently — updating `last_active_on`.
4. During an active session, the client writes a heartbeat to `last_active_at` every 30 seconds.

This avoids true multi-writer conflicts without requiring distributed locks.

#### Dialogue carryover (Feature 3 extension)

Dialogue simulation sessions are stateful — the LLM conversation has a history. For carryover:

| Column | Type | Purpose |
|--------|------|---------|
| `dialogue_history` | json | Array of `{role, content, timestamp}` turns |
| `dialogue_scenario` | text | The scenario prompt that seeded this dialogue |
| `dialogue_corrections` | json | Agent corrections issued during the dialogue |

When the phone client picks up a dialogue mid-conversation, it loads `dialogue_history` and replays it as context to the LLM, then continues seamlessly. The user sees a summary:

> *Continuing your dialogue: "Asking your landlord about lease terms"*
> *You've exchanged 4 messages. Here's where you left off:*
>
> Landlord: 好的，押金是两个月的房租。
> Your turn: _

### Infrastructure requirements

| Component | What's needed | Complexity |
|-----------|---------------|------------|
| **State store** | DynamoDB (or Postgres) for `review_sessions`, `term_confidence`, etc. | Medium — aligns with existing migration plan |
| **Telegram bot** | Long-polling or webhook listener; `python-telegram-bot` library | Low — thin wrapper over review engine |
| **Review engine API** | Internal Python API (`start_session`, `record_rating`, `get_next_term`, `get_active_session`) that both CLI and bot call | Medium — this is Feature 2's core logic, client-agnostic |
| **Hosting** | A lightweight always-on process for the Telegram bot (EC2 nano, Lambda + API Gateway, or a container) | Low — Telegram long-polling needs minimal compute |
| **Auth** | Telegram: bot token + chat_id allowlist. No user auth system needed for single-user. | Low |

### What this means for Feature 1–3 implementation

The key insight is that **the review engine must be client-agnostic from the start**. Instead of building Features 1–3 as CLI-coupled code, the implementation should follow a two-layer architecture:

```
┌────────────────┐  ┌────────────────┐  ┌────────────────┐
│   CLI client   │  │ Telegram client │  │  Web client    │
│   (terminal)   │  │   (bot API)    │  │  (PWA, later)  │
└───────┬────────┘  └───────┬────────┘  └───────┬────────┘
        │                   │                   │
        └───────────┬───────┴───────────────────┘
                    │
            ┌───────▼────────┐
            │  Review Engine  │
            │  (Python API)   │
            │                 │
            │  • scheduling   │
            │  • sessions     │
            │  • content gen  │
            │  • state r/w    │
            └───────┬────────┘
                    │
            ┌───────▼────────┐
            │  State Store    │
            │  (DynamoDB or   │
            │   SQLite+sync)  │
            └────────────────┘
```

Each client is a thin adapter that translates its native interaction model (terminal I/O, Telegram messages, HTTP requests) into calls on the review engine. The engine owns all business logic and state. This means:

- **Feature 2 (interactive sessions)** is implemented as engine methods, not as a CLI input loop. The CLI client wraps those methods in `input()` / `print()` calls; the Telegram client wraps them in message handlers.
- **Feature 3 (content generation)** is a pure engine concern — clients just display what the engine returns.
- **Feature 1 (reminders)** is a scheduled job in the engine that emits notifications. The CLI client is one delivery channel; Telegram is another. Both read from the same schedule.

---

## Summary of Recommendations

| Feature | Recommended approach | Rationale |
|---------|---------------------|-----------|
| Reminders | Daily digest (CLI + Telegram) | Lowest friction, push notifications on phone, richest info |
| Review sessions | Client-agnostic engine, CLI + Telegram adapters | Enables session carryover between laptop and phone |
| Content generation | Sentence drills (recognition → fill-blank → production) | Atomic, graduated, generates well from LLM |
| Cross-device access | Telegram bot (iteration 1), PWA (iteration 2) | Zero-install mobile access, natural chat UX for dialogues |
| Conversation carryover | Server-backed session state with device handoff | Seamless resume across CLI ↔ Telegram ↔ web |

---

## Iteration sequence

1. **Scaffold `vocab-review-agent`** — depends on core, empty CLI with `review` subcommand
2. **Implement review engine** — client-agnostic Python API: `start_session`, `record_rating`, `get_next_term`, `get_active_session`
3. **Implement scheduling engine** — per-topic interval tracking, "due" calculation
4. **Implement CLI client** — thin adapter over the review engine using terminal I/O
5. **Implement daily digest** (Feature 1) — engine emits notification payload; CLI prints it, Telegram sends it
6. **Implement interactive review session** (Feature 2) — engine manages session state; CLI client wraps in `input()`/`print()`
7. **Implement sentence drill generation** (Feature 3) — LLM generates drills per-term, cached in state store
8. **Connect scheduling ↔ sessions** — completing a session updates schedule state
9. **Migrate review state to DynamoDB** — prerequisite for cross-device; CLI keeps local SQLite cache for offline
10. **Implement Telegram bot client** — thin adapter over the same review engine; inline keyboards for ratings
11. **Implement conversation carryover** — session handoff protocol, dialogue history replay
12. **Add adaptive session behaviour** — skip easy terms, surface struggling terms
13. **Add dialogue mode** — real-time LLM conversation using topic terms (works on both CLI and Telegram)
14. **Add PWA client** (optional) — for richer display (stroke order, audio) if Telegram proves limiting

---

## Open questions for next iteration

1. ~~**Notification delivery**: Is CLI stdout sufficient for reminders, or do we need Telegram/Discord/email from day one?~~ **Resolved (iteration 2)**: Telegram bot is the recommended mobile delivery channel. CLI stdout for laptop, Telegram for phone. Both from iteration 1 of the review engine.
2. ~~**Multi-device**: Will the user only review from their development machine, or also mobile?~~ **Resolved (iteration 2)**: Yes, mobile is a requirement. The review engine is client-agnostic; Telegram bot provides phone access. Session state moves to DynamoDB for cross-device access (iteration step 9).
3. **Confidence model**: Simple interval-based (like SM-2) or something lighter? How much should it overlap/conflict with Anki's own SRS state?
4. **Content caching**: Pre-generate drills during extraction (batch, cheaper) or generate on-demand during sessions (fresher, more adaptive)?
5. **Relationship to Anki**: Should review sessions mark terms as "reviewed" in a way that Anki can see (e.g., tagging), or are the two systems intentionally independent?
6. **Session length**: Fixed (always all terms in a topic) or time-boxed ("10 minutes of Housing")? Time-boxed is more realistic but needs term prioritisation logic.
7. **Telegram bot hosting**: Long-polling (simpler, works behind NAT) or webhook (lower latency, needs public URL)? Long-polling is sufficient for a single-user bot.
8. **Offline session sync**: If the user completes a review on the CLI while offline, how does it reconcile with the server state? Last-write-wins per-session (sessions are single-user, so conflicts are device-switch races, not true multi-user conflicts) or merge per-term ratings?
