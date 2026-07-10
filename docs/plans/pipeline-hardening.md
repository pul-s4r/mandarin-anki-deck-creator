# Pipeline Hardening Plan

**Date**: 2026-07-05  
**Status**: approved  
**Scope**: `src/anki_deck_generator/pipeline.py` and imported modules (excluding library modules)

## Context

MSR + arch-quality review identified 1 CRITICAL and 6 WARNING/NOTE findings in the core pipeline flow. All 7 items are scoped for implementation.

## Execution order

1. Item 1 (foundational — changes return types)
2. Item 5 (depends on Item 1 signatures)
3. Items 2, 3, 4, 6 (independent — batch in any order)
4. Item 7 (cleanup last)

---

## Item 1 — Surface LLM failures in stats (CRITICAL)

**Problem**: `_fallback_json_invoke` returns `[]` on JSON parse/validation failure. `translate_simplified_terms` returns `{}` on failure. No chunk-level failure tracking. User pays for LLM calls, gets fewer cards, has no indication anything went wrong.

**Changes**:
- `llm/bedrock_chain.py` — `_fallback_json_invoke` returns `tuple[list[LlmVocabularyItem], bool]` where bool indicates success. Same for `translate_simplified_terms` → `tuple[dict[str, str], bool]`.
- `pipeline_types.py` — add `chunks_failed: int = 0` and `translation_fallback_failed: bool = False` to `PipelineStats`.
- `pipeline.py` — two call sites (`extract_llm_vocabulary_items` lines ~143, ~179) increment `chunks_failed` on `success=False`. `finish_pipeline_after_llm` tracks `translation_fallback_failed`.
- `llm/fixture_player.py` — `vocabulary_for_chunk` and `translations_for_terms` updated to return tuples.

**Tests**:
- New test: failed chunk produces `chunks_failed > 0`
- Update existing E2E mocks to return `(result, True)` tuples

**Estimated effort**: 15 min

---

## Item 2 — Retry wrapper on Bedrock (WARNING)

**Problem**: No retry logic on `model.invoke()`. Single transient error (5xx, throttle, timeout) = permanent chunk loss.

**Changes**:
- `config/settings.py` — add `bedrock_retry_max_attempts: int = 2` and `bedrock_retry_delay: float = 1.0`.
- `llm/bedrock_chain.py` — wrap `model.invoke(messages)` in a simple retry loop (3 attempts max, exponential backoff) for `ChatBedrockConverse` only. `FixtureLlmModel` bypasses retry.

**Tests**:
- Mock first invoke raises, second succeeds → verify retry fires and returns cards

**Estimated effort**: 10 min

---

## Item 3 — Cache `segment_table_blocks` (WARNING)

**Problem**: Called 3 times on same text — `extract_llm_vocabulary_items` (line 105), `list_llm_text_units` → llm_units.py:30, `finish_pipeline_after_llm` (line 208). Triple O(n) scan + divergence risk.

**Changes**:
- `pipeline.py` — add optional `blocks: list[TextBlock] | None = None` to both `extract_llm_vocabulary_items` and `finish_pipeline_after_llm`. If `None`, compute inline (backward compat). `run_pipeline_from_text` computes once, passes through.
- `sync/orchestrator.py` — no change (passes `None`, gets backward-compat behavior).

**Tests**:
- Existing tests unaffected. Add regression verifying blocks passthrough.

**Estimated effort**: 10 min

---

## Item 4 — Wrap CEDICT loading (WARNING)

**Problem**: `FileLineDictionarySource` + `DictionaryIndex.from_source` can raise `OSError` or `UnicodeDecodeError`. No try/except — entire pipeline crashes mid-enrichment.

**Changes**:
- `pipeline.py` lines ~217-228 — wrap CEDICT source/index construction in try/except `(OSError, UnicodeDecodeError)`. Log at ERROR, fall through to "no CEDICT" path (skip enrichment).

**Tests**:
- Test with unreadable CEDICT file → enrichment skipped, no crash, `enriched_count == 0`

**Estimated effort**: 5 min

---

## Item 5 — `LlmClient` Protocol (WARNING)

**Problem**: `isinstance(model, FixtureLlmModel)` branching couples `bedrock_chain.py` to concrete types. Adds new provider requires modifying `bedrock_chain.py`.

**Changes**:
- `llm/bedrock_chain.py` — define `LlmClient` Protocol with `vocabulary_for_chunk(text: str) -> tuple[list[LlmVocabularyItem], bool]` and `translate_terms(terms: list[str]) -> tuple[dict[str, str], bool]`.
- `llm/fixture_player.py` — implement Protocol methods directly.
- `llm/bedrock_chain.py` — create thin `_BedrockLlmClient` adapter wrapping `ChatBedrockConverse` that implements the Protocol.
- `pipeline.py` — type hint `model: LlmClient` instead of union type. Remove all `isinstance` checks.

**Tests**:
- Fixture tests exercise Protocol path. No behavioral change.

**Estimated effort**: 15 min

---

## Item 6 — Fix table mutation of Pydantic model (WARNING)

**Problem**: `preprocess/tables.py:48-51` mutates `.pinyin` and `.meaning` on `LlmVocabularyItem` post-construction. Model is not frozen but mutation bypasses validators. Intent undocumented.

**Changes**:
- `llm/schemas.py` — add `model_config = ConfigDict(extra="ignore")` to `LlmVocabularyItem` with docstring noting that table parser legitimately mutates fields post-construction for continuation lines.

**Tests**:
- Verify pinyin continuation line mutation still works

**Estimated effort**: 5 min

---

## Item 7 — `PipelineContext` dataclass (NOTE)

**Problem**: `extract_llm_vocabulary_items` takes 4 callback parameters. Adding more grows signature indefinitely.

**Changes**:
- `pipeline.py` — define `@dataclass class PipelineContext` grouping `progress_callback`, `should_run_llm`, `load_cached_chunk_cards`, `on_chunk_processed`.
- Replace individual params with `ctx: PipelineContext | None = None`. Access via `ctx.progress_callback` etc.
- `sync/orchestrator.py` — construct `PipelineContext(...)` at call site.

**Tests**:
- Existing E2E tests adapt to new signature

**Estimated effort**: 10 min

---

## Total estimated effort: ~65 min

## Verification

After all items: `pytest -q` passes, `ruff check src/ tests/` clean.
