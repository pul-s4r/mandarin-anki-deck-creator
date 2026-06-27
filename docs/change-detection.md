# Change detection

How the pipeline decides **what to re-process** on incremental runs (`schedule`, Drive cron, or webhook Mode B). All paths use the same logic inside `run_incremental_sync`.

## Overview: three skip layers

```
Document changed?  ──no──► skip entire file (no download / no LLM)
        │
       yes
        ▼
Download / read text → chunk text
        │
        ▼
Per chunk: hash changed?  ──no──► reuse cached card IDs (no LLM for that chunk)
        │
       yes
        ▼
Run LLM on chunk → merge cards into StateStore
```

Implementation: `src/anki_deck_generator/sync/change_detection.py` (two small functions) and `src/anki_deck_generator/sync/orchestrator.py` (orchestration).

## Layer 1 — Document skip

**Question:** Has this source file changed since we last processed it?

| Source type | Signal stored | Skip when |
|-------------|---------------|-----------|
| **Local filesystem** | `SourceRecord.content_sha256` = SHA-256 of **raw file bytes** | New hash equals stored hash |
| **Google Drive** | `SourceRecord.revision_id` + `etag` from Drive metadata | Both match stored values (before download) |
| **Google Drive (belt-and-braces)** | Same `content_sha256` after download | Raw exported bytes hash unchanged even if revision moved |

Local resolution: `sync/source_resolution.py` → `resolve_local_file_source`.

Drive resolution: `sync/orchestrator.py` → `_drive_revision_unchanged`, then optional `should_skip_document_by_stored_hash` after download.

```python
# change_detection.py
def should_skip_document_by_stored_hash(previous, stored_content_sha256) -> bool:
    return previous is not None and previous.content_sha256 == stored_content_sha256
```

**Important:** For local files, the document hash is over **file bytes**, not extracted text. Renaming or re-saving without content change still skips correctly.

## Layer 2 — Chunk skip (partial document changes)

**Question:** Within an edited document, which text chunks need fresh LLM calls?

1. Preprocess produces a sequence of LLM text units (`preprocess/llm_units.py`).
2. Each unit has a stable `chunk_sha256` over normalized chunk text.
3. `ChunkRecord` rows store `(source_id, chunk_index, chunk_sha256, llm_output_card_ids)`.
4. Before calling Bedrock for chunk *N*, the orchestrator asks:

```python
def chunk_needs_llm(previous, chunk_sha256) -> bool:
    if previous is None:
        return True
    return previous.chunk_sha256 != chunk_sha256
```

If the hash is unchanged, cached card IDs from the prior run are loaded and **no LLM call** is made for that chunk.

**This is the “partial document changes” behavior.** Editing one section of a long PDF only re-processes the chunks whose text changed (plus any new chunks from length shifts).

Report fields: `SyncReport.stats.chunks_processed` vs `chunks_skipped`.

## Layer 3 — Card upsert (vocabulary dedup)

After LLM + enrichment, cards are keyed by simplified headword (`StateStore.upsert_card`). Semantic fields are hashed in `CardRecord.content_hash`.

| Result | Meaning |
|--------|---------|
| `created` | New headword |
| `updated` | Same key, different content |
| `unchanged` | Same key and same content hash |

Per-source outcomes appear in `SyncReport.outcomes[]`.

## Triggers: same logic, different “when”

Change detection does **not** depend on how the run was started:

| Trigger | How run starts | Change detection |
|---------|----------------|------------------|
| `schedule` (manual or cron) | CLI polls Drive metadata or reads local files | Layers 1–3 |
| `drive-push` (webhook Mode B) | `process-pending` after debounce | Layers 1–3, scoped to `only_file_ids` |
| `api-upload` | HTTP file upload | Persists cards; no document/chunk skip for that upload |

**Drive polling vs webhooks:** Both end in `run_incremental_sync`. Polling = run `schedule` on a timer. Webhooks = faster notification + optional edit debounce (`pending_edits`). Partial chunk reuse works the same either way.

`edit_settling` in source-set YAML applies to the **webhook debounce path only**, not to plain `schedule` polling.

## State records (what gets persisted)

| Record | Key fields for change detection |
|--------|--------------------------------|
| `SourceRecord` | `revision_id`, `etag`, `content_sha256`, `last_ingested_at` |
| `ChunkRecord` | `chunk_index`, `chunk_sha256`, `llm_output_card_ids` |
| `CardRecord` | `content_hash`, `last_updated_at` |

Schema: `src/anki_deck_generator/state/records.py`.

## Anki delivery skip (separate from ingest)

After cards are in `StateStore`, Anki export tracks:

- `ankiweb_note_id`
- `ankiweb_last_synced_at`
- `ankiweb_last_synced_fields`

Direct export (`type: anki`) and the pull agent both use three-way merge against these fields so user edits in Anki are not silently overwritten. See `export/ankiweb/merge.py`.

## Debugging checklist

| Symptom | Check |
|---------|--------|
| Whole document re-processed every run | `sources.content_sha256` / `revision_id` — did metadata or bytes actually change? |
| LLM runs on every chunk despite small edit | Chunk boundaries may have shifted (`chunk_size` / `chunk_overlap` settings) |
| Document skipped but you expected changes | File bytes unchanged (local) or Drive revision unchanged |
| Drive doc never picked up | OAuth token, folder ID, or cron not running `schedule` |

Inspect state:

```bash
sqlite3 "$STATE_DB" "SELECT provider, external_id, revision_id, content_sha256 FROM sources;"
sqlite3 "$STATE_DB" "SELECT source_id, chunk_index, chunk_sha256 FROM chunks LIMIT 20;"
anki-notes-pipeline state list-runs --db-path "$STATE_DB"
```

## Code references

| File | Role |
|------|------|
| `sync/change_detection.py` | Document hash + chunk hash helpers |
| `sync/source_resolution.py` | Local file read + document skip |
| `sync/orchestrator.py` | Drive metadata skip, chunk cache, LLM dispatch |
| `preprocess/fingerprints.py` | `sha256_bytes`, `sha256_utf8` |
| `preprocess/llm_units.py` | Chunk sequence + per-chunk hashes |
| `state/sqlite_store.py` | Persistence |
