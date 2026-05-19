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

## Shared Infrastructure with the Deck Generator

The deck generator's serverless deployment (§§9–18 of the [architecture plan](./architecture/web-server-and-integrations-plan.md)) establishes an AWS stack: Lambda container images, EventBridge Scheduler, API Gateway, SQS FIFO, DynamoDB, and Secrets Manager. The review agent can reuse most of this infrastructure. This section confirms what is shared, what diverges, and what the operational differences are.

### What is shared

| Component | Generator usage | Review agent usage | Sharing model |
|-----------|-----------------|-------------------|---------------|
| **AWS account** | Bedrock LLM, Lambda, DynamoDB | Bedrock LLM, Lambda, DynamoDB | Same account — single billing, shared IAM |
| **DynamoDB** | `cards`, `tags`, `sources`, `chunks`, `pending_edits`, `drive_channels` | `review_sessions`, `review_schedule`, `term_confidence`, `generated_content`, `notification_log` | Same service, **separate table namespace** (e.g. `generator-*` vs `review-*`). No cross-table writes. |
| **Bedrock** | Heavy — vocabulary extraction from notes (batch, multi-chunk) | Lighter per call — sentence drills, dialogue turns, translation evaluation | Same model, same credentials, same `LlmClient` protocol from `anki-pipeline-core` |
| **Secrets Manager / SSM** | Google Drive OAuth refresh token, Bedrock config | Telegram bot token, Bedrock config | Same store; separate secret paths per app |
| **Lambda container base** | `public.ecr.aws/lambda/python:3.12` + pipeline deps | Same base + review engine deps (lighter — no CEDICT, no PDF/DOCX parsers) | Separate images, shared base layer in ECR. Generator image is larger (~120 MB CEDICT). |
| **EventBridge Scheduler** | Cron per source set (weekly Drive refresh) | Cron for daily digest + Mode B tick (1-min poll for pending review actions) | Same service, separate schedule groups |
| **API Gateway** | Drive webhook endpoint (`POST /drive/notifications`) | Telegram webhook endpoint (`POST /telegram/webhook`), or unused if Telegram uses long-polling | Same API Gateway instance with separate routes, or separate APIs |
| **`anki-pipeline-core`** | Data models, state protocol, LLM client, normalisation | Same | Shared Python package, bundled in both container images |

### What diverges: operational profile comparison

The fundamental difference is **batch-event vs interactive**. The generator processes documents in bulk, infrequently. The review agent handles many small user interactions throughout the day.

| Dimension | Deck Generator | Review Agent |
|-----------|----------------|--------------|
| **Trigger pattern** | Cron (weekly) + Drive webhook (sporadic edits) | User interaction (each tap/message) + daily digest cron |
| **Invocation frequency** | Low: a few times per week | High: dozens of small invocations per day during active study |
| **Execution duration** | Long: seconds to minutes per run (multi-chunk LLM extraction) | Short: sub-second per interaction (DB read + format response) |
| **Latency requirement** | None — async batch; user checks results later | Strict — Telegram expects response within ~2s; interactive sessions need sub-second feel |
| **LLM cost per invocation** | High — extracts vocabulary from full document chunks | Low to medium — generates one sentence drill or evaluates one dialogue turn |
| **Cold start sensitivity** | Low — minutes-long runs amortize 5–10s cold start; CEDICT load from S3 adds ~3s | High — a 5s cold start on a Telegram response feels broken. Container image is lighter (no CEDICT), but Lambda cold starts still matter. |
| **State write pattern** | Bulk: upsert many cards + chunks after a full pipeline run | Incremental: one confidence rating, one session-progress update per interaction |
| **Concurrency model** | Serialized per source set (SQS FIFO `MessageGroupId`); no parallel processing of same channel | Single user, but concurrent clients (CLI + Telegram) access same session state; liveness-window protocol prevents true concurrency |
| **Failure recovery** | Retry-safe: PendingEdits absorbs duplicates; content-hash dedup makes reprocessing a no-op | Session-aware: failed interaction leaves session in last-known-good state; client retries are safe because `record_rating` is idempotent on `(session_id, term_id)` |
| **Debounce** | Yes — `quiet_minutes` + `max_delay_minutes` to coalesce rapid Drive edits before expensive LLM work | No — user interactions are intentional, not bursty; each one should be processed immediately |
| **Data dependencies at runtime** | CEDICT dictionary (~120 MB), source documents, prior card state | Vocabulary terms (read from generator's cards via core `StateStore`), session state, cached drill content |
| **Container image size** | Large: CEDICT, PyMuPDF, python-docx, langchain-aws | Small: review engine, `python-telegram-bot` or `httpx`, langchain-aws (for drill generation only) |

### Lambda hosting model differences

The generator's two-Lambda design (webhook receiver + unified sync Lambda) maps cleanly to the review agent, but with different trade-offs per component:

**Generator Lambda topology (from §18):**

```
Drive webhook → API Gateway → Webhook Receiver Lambda (thin, fast)
                                     ↓ SQS FIFO
                              Unified Sync Lambda
                                Mode A: pull_changes (changes.list → PendingEdits)
                                Mode B: process_pending (run_sync → cards)
EventBridge (weekly cron) → Unified Sync Lambda (Mode B)
```

**Review agent Lambda topology (equivalent):**

```
Telegram webhook → API Gateway → Telegram Receiver Lambda (thin, fast)
                                       ↓ (inline or SQS)
                                 Review Engine Lambda
                                   • get_active_session / start_session
                                   • record_rating / get_next_term
                                   • generate_drill (Bedrock call)
                                   → respond via Telegram Bot API
EventBridge (daily cron) → Digest Lambda (reads schedule, sends Telegram message)
EventBridge (1-min tick) → Review Engine Lambda (Mode B: check for abandoned sessions, send reminders)
```

**Key difference: inline vs queued processing.** The generator must queue work because `run_sync` takes minutes. The review agent can often respond inline within the Telegram webhook handler because most operations (read session, return next term) are sub-100ms DynamoDB reads. Only drill generation (Bedrock call) might need async handling.

| Component | Generator | Review Agent | Difference |
|-----------|-----------|-------------|------------|
| Webhook receiver | Must return 200 in <2s (Drive SLA) → enqueue to SQS | Must return 200 in <2s (Telegram spec) → can often respond inline | Review agent may skip the SQS hop for simple reads |
| Worker Lambda | Minutes-long execution; invoked from SQS | Sub-second for most operations; seconds for LLM drill generation | Review agent Lambda timeout can be much shorter (30s vs 5min) |
| SQS FIFO | Required — serializes per-channel, absorbs duplicate Drive pings | Optional — only needed if drill generation is deferred to avoid Telegram timeout | Review agent can start without SQS and add it only for LLM-heavy operations |
| EventBridge | Weekly cron + 1-min tick for pending processing | Daily digest + optional 1-min tick for session cleanup / reminders | Same mechanism, different frequencies |
| Cold start mitigation | Provisioned concurrency if needed (heavy image) | Less critical (lighter image), but consider Lambda SnapStart or provisioned concurrency for interactive feel | Review agent benefits more from warm pools due to latency sensitivity |

### Cold start scenarios: which user actions are affected

AWS Lambda evicts idle containers after roughly 10–15 minutes of inactivity. A cold start means the runtime initialises from scratch — importing Python modules, establishing DynamoDB connections, and (for the generator) loading CEDICT from S3. For the review agent's lighter image, cold start is estimated at 1–3 seconds; for the generator, 5–10 seconds.

The critical question is: **which user-initiated actions land on a cold Lambda, and what does the user experience?**

#### Actions that trigger a cold start

| # | User action | Lambda hit | Cold start likely? | User-perceived impact |
|---|-------------|-----------|-------------------|----------------------|
| 1 | **First Telegram interaction of the day** — user opens chat after overnight inactivity, taps "Start review" or sees the daily digest reply buttons and taps one | Review Engine Lambda | **Yes** — container has been idle for hours | 1–3s delay before the bot responds with the first term. Most noticeable cold start. |
| 2 | **Resuming after a gap mid-session** — user reviews 4 terms, puts the phone down for 20+ minutes, picks it back up and taps the next confidence rating | Review Engine Lambda | **Yes** — container was evicted during the idle gap | 1–3s delay on what should feel like a continuation. Jarring because the previous interactions were instant. |
| 3 | **Requesting a new drill or dialogue** — user finishes a review session and taps "Generate sentence drill" or "Start dialogue" | Review Engine Lambda (Bedrock call) | Possibly — depends on whether a prior interaction warmed the container | If cold: 1–3s Lambda init + 2–5s Bedrock LLM call = 3–8s total. The LLM latency dominates regardless, so cold start is less noticeable here. |
| 4 | **Tapping the daily digest notification** — the scheduled digest arrives as a Telegram message; user taps an inline button ("Review Housing & Rent") | Review Engine Lambda | **Likely** — the digest was sent by a separate scheduled invocation; the user taps minutes or hours later | 1–3s delay. Same as scenario 1. |
| 5 | **CLI `vocab review start`** — user opens terminal and starts a review session | Review Engine Lambda (if CLI talks to the cloud engine) or local-only (if CLI uses local SQLite cache) | **Depends on architecture**: if the CLI calls the Lambda API, same cold-start risk. If CLI runs the review engine locally, no Lambda involved. | 0s (local) or 1–3s (remote). Design should prefer local engine for CLI. |

#### Actions that do NOT trigger a cold start

| # | Action | Why no cold start |
|---|--------|-------------------|
| A | **Rapid-fire taps during an active session** — user reviews terms 1, 2, 3, 4 in quick succession (each tap 5–15 seconds apart) | Container stays warm between interactions. Each response is sub-100ms (DynamoDB read + Telegram Bot API call). |
| B | **Daily digest delivery** (the cron job itself) | System-initiated via EventBridge. Cold start happens on the *cron Lambda*, not on a user-facing interaction. The user sees the message arrive in Telegram — they don't wait for it. |
| C | **Session cleanup / heartbeat check** | System-initiated background tick. No user waiting. |

#### The worst-case user experience

The most problematic scenario is **#2 — resuming after a mid-session gap**. The user tapped "2-ok" for term 4, got an instant response showing term 5, then put the phone in their pocket. Twenty minutes later they pull it out and tap "Reveal answer" — and wait 2 seconds for what was previously instant. The mental model breaks because the interaction *felt* like a continuous session.

Scenario #1 (first interaction of the day) is less jarring because the user is initiating a new action — they expect a brief loading moment when opening something fresh.

#### Mitigations

| Mitigation | Effect | Cost / complexity |
|------------|--------|-------------------|
| **Provisioned concurrency (1 instance)** | Eliminates cold starts entirely. One container is always warm. | ~$5–8/month for a small Lambda. Defeats scale-to-zero but is cheap for a single-user app. |
| **EventBridge warm-ping** | A 5-minute scheduled ping invokes the Lambda with a no-op event, keeping the container warm during study hours (e.g., 6 PM–10 PM). | Near-zero cost (a few hundred invocations/month). Requires a "warm-ping" event type the handler recognises and short-circuits. |
| **Telegram "typing" indicator** | On webhook receipt, immediately call `sendChatAction(action="typing")` before invoking the engine Lambda. The user sees "Bot is typing..." which sets the expectation that a response is coming. | Zero cost. Doesn't reduce latency, but significantly improves perceived responsiveness. |
| **Pre-warm on digest send** | After the daily digest Lambda sends the Telegram message, it also invokes the Review Engine Lambda with a warm-ping. By the time the user taps a button (seconds to minutes later), the container is warm. | Near-zero cost. Covers scenario #4 well; doesn't help with scenario #2. |
| **Migrate to Fargate** | Persistent process, no cold starts ever. | ~$9/month. The nuclear option — use only if cold starts prove unacceptable after trying the above. |

**Recommended approach**: Start with the **"typing" indicator** (free, immediate UX improvement) and the **EventBridge warm-ping during study hours** (near-zero cost, covers the common case). Add provisioned concurrency only if measured P95 latency exceeds acceptable thresholds.

#### Comparison to the deck generator

The generator does not have a cold-start problem because no user waits for its Lambda to respond. Drive webhook pings are acknowledged by the thin Receiver Lambda (no heavy init), and the Sync Lambda runs asynchronously — the user checks results later. A 5–10 second cold start on a minutes-long batch job is invisible.

This is the sharpest operational difference between the two: **the generator's cold starts are free; the review agent's cold starts are the primary latency risk.**

### Alternative: long-polling instead of Lambda for Telegram

If Telegram uses **long-polling** instead of webhooks, the review agent needs a persistent process (not Lambda). Options:

| Option | Pros | Cons |
|--------|------|------|
| **ECS Fargate task** (0.25 vCPU, 0.5 GB) | Always-on, no cold start, simple `python-telegram-bot` loop | ~$9/month; not scale-to-zero |
| **EC2 `t4g.nano`** | Cheapest always-on ($3/month), full control | Manual patching, not serverless |
| **Lambda + webhook** (as above) | Scale-to-zero, no always-on cost | Cold start latency; needs API Gateway + public URL |

**Recommendation**: Start with **Lambda + Telegram webhook** mode to match the generator's serverless model. The Telegram Bot API supports both modes — `setWebhook` for Lambda, `getUpdates` long-polling for a persistent process. If cold-start latency proves unacceptable for interactive sessions, migrate to a Fargate task for the Telegram client while keeping the review engine and state store unchanged.

### Shared IaC and deployment

Both apps can share a single IaC project (CDK, SAM, or Terraform) with separate stacks or modules:

```
infra/
├── shared/
│   ├── dynamodb.tf          # table definitions for both apps
│   ├── secrets.tf           # Secrets Manager paths
│   └── ecr.tf               # shared ECR repository
├── generator/
│   ├── lambda_webhook.tf    # Drive webhook receiver
│   ├── lambda_sync.tf       # Unified sync Lambda
│   ├── api_gateway.tf       # /drive/notifications route
│   ├── eventbridge.tf       # weekly cron + 1-min tick
│   └── sqs.tf               # FIFO queue
└── review-agent/
    ├── lambda_telegram.tf   # Telegram webhook receiver / engine
    ├── api_gateway.tf       # /telegram/webhook route (or add route to shared API)
    └── eventbridge.tf       # daily digest + session cleanup
```

This keeps deployment independent (updating the review agent doesn't redeploy the generator) while sharing the DynamoDB and secrets infrastructure.

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
