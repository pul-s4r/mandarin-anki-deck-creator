from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_aws import ChatBedrockConverse

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.llm.fixture_player import FixtureLlmModel
from anki_deck_generator.llm.schemas import (
    LlmTranslationBatch,
    LlmVocabularyItem,
    LlmVocabularyResult,
    llm_translation_batch_json_schema_text,
    llm_vocabulary_response_json_schema_text,
)

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


# ── Item 5: LlmClient Protocol ──────────────────────────────────────────────


class LlmClient(Protocol):
    """Interface for LLM providers used by the pipeline."""

    def vocabulary_for_chunk(self, text: str) -> tuple[list[LlmVocabularyItem], bool]: ...

    def translate_terms(self, terms: list[str]) -> tuple[dict[str, str], bool]: ...


# ── Item 5: _BedrockLlmClient adapter ───────────────────────────────────────


class _BedrockLlmClient:
    """Adapter wrapping ChatBedrockConverse to implement LlmClient Protocol."""

    def __init__(self, model: ChatBedrockConverse) -> None:
        self._model = model

    def vocabulary_for_chunk(self, text: str) -> tuple[list[LlmVocabularyItem], bool]:
        return (_fallback_json_invoke(self._model, text), True)

    def translate_terms(self, terms: list[str]) -> tuple[dict[str, str], bool]:
        return (_translate_bedrock(self._model, terms), True)


# ── Message helpers ─────────────────────────────────────────────────────────


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


_SYSTEM_PROMPT = """You are extracting Mandarin vocabulary flashcards from raw study notes. Reply with a single JSON object only (no markdown fences, no commentary); it must validate against the JSON Schema appended to the user message. Do not extract, summarize, or output example sentences, dialogue lines, or multi-clause Chinese passages as separate fields; omit them entirely. If a line is primarily an example sentence rather than a headword or pattern, skip it unless it contains a clear vocabulary item that can be turned into one card row (headword + gloss) without treating the whole sentence as a field. Use simplified Chinese for simplified unless the note clearly targets traditional. Infer part_of_speech when possible (noun, verb, adjective, adverb, measure_word, idiom, phrase, grammar_pattern, sentence_pattern, etc.—multiple allowed, use semicolons). If the line is a grammar template, include grammar_pattern in part_of_speech and put the pattern explanation in usage_notes. Merge sub-items (a./b.) into meaning separated by ';'. If pinyin appears as tone numbers, convert to standard pinyin with tone marks when you can; otherwise preserve and note in usage_notes. If English or pinyin is missing and cannot be inferred, leave meaning or pinyin empty (do not invent). Ignore lesson metadata lines that are only dates, payments, or chat unless they contain vocabulary. Do not include Sentence* or example-sentence fields."""

_USER_TEMPLATE = """Here is plain text from Chinese study notes (possibly with dates and numbering). Extract one card per distinct vocabulary item, phrase, or grammar point. Be exhaustive: include all vocabulary items present in the text and do not stop early.

{chunk_text}
"""

_TRANSLATION_SYSTEM_PROMPT = """You translate simplified Chinese vocabulary headwords into concise English glosses for flashcards. Reply with a single JSON object only (no markdown fences, no commentary); it must validate against the JSON Schema appended to the user message. Include exactly one translation object per input term. Keep each `simplified` field identical to the input string (same characters). Prefer short dictionary-style English; for transparent compounds a short phrase is fine. Do not include pinyin or example sentences."""

_TRANSLATION_USER_TEMPLATE = """Translate every term below.

Terms (one per line):
{terms}

JSON Schema for your response (conform exactly):
{schema}
"""


# ── Item 2: Retry helper ────────────────────────────────────────────────────


def _retry_invoke(
    model: ChatBedrockConverse,
    messages: list[BaseMessage],
    *,
    max_attempts: int = 3,
    delay: float = 1.0,
    multiplier: float = 2.0,
) -> Any:
    """Invoke Bedrock with exponential-backoff retry for transient errors."""
    for attempt in range(max_attempts):
        try:
            return model.invoke(messages)
        except Exception:
            if attempt == max_attempts - 1:
                raise
            logger.warning(
                "Bedrock invoke attempt %d/%d failed, retrying in %.1fs",
                attempt + 1,
                max_attempts,
                delay,
            )
            time.sleep(delay)
            delay *= multiplier


# ── Builder ─────────────────────────────────────────────────────────────────


def build_bedrock_model(settings: Settings) -> LlmClient:
    fp = settings.llm_fixture_path
    if fp is not None and fp.is_file():
        return FixtureLlmModel.from_path(fp)
    kwargs: dict[str, Any] = {
        "model_id": settings.bedrock_model_id,
        "temperature": settings.bedrock_temperature,
        "max_tokens": settings.bedrock_max_tokens,
    }
    if settings.aws_region:
        kwargs["region_name"] = settings.aws_region
    if settings.bedrock_top_p is not None:
        kwargs["top_p"] = settings.bedrock_top_p
    return _BedrockLlmClient(ChatBedrockConverse(**kwargs))


# ── Item 1 + 5: Unified public API ─────────────────────────────────────────


def extract_vocabulary_from_chunk(
    model: LlmClient, chunk_text: str
) -> tuple[list[LlmVocabularyItem], bool]:
    """Extract vocabulary from a text chunk via the LlmClient protocol."""
    return model.vocabulary_for_chunk(chunk_text)


def translate_simplified_terms(
    model: LlmClient, terms: list[str]
) -> tuple[dict[str, str], bool]:
    """Translate simplified terms via the LlmClient protocol."""
    return model.translate_terms(terms)


# ── Internal Bedrock implementations ────────────────────────────────────────


def _fallback_json_invoke(model: ChatBedrockConverse, chunk_text: str) -> list[LlmVocabularyItem]:
    human = _USER_TEMPLATE.format(chunk_text=chunk_text)
    human += (
        "\n\nJSON Schema for your response (conform exactly; property descriptions are authoritative):\n"
        + llm_vocabulary_response_json_schema_text()
    )
    messages = [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=human)]
    try:
        raw = _retry_invoke(model, messages)
    except Exception:
        logger.exception("Bedrock invoke failed permanently for chunk (len=%d)", len(chunk_text))
        return []
    text = _message_content_to_text(raw.content)
    text = _FENCE.sub("", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        recovered = _extract_first_json_object(text)
        if recovered is None:
            logger.error("JSON fallback parse failed; snippet=%s", text[:200])
            return []
        try:
            data = json.loads(recovered)
        except json.JSONDecodeError:
            logger.error("JSON fallback recovery parse failed; snippet=%s", recovered[:200])
            return []
    try:
        result = LlmVocabularyResult.model_validate(data)
        return list(result.cards)
    except Exception:
        logger.exception("validate fallback JSON failed")
        return []


def _translate_bedrock(model: ChatBedrockConverse, terms: list[str]) -> dict[str, str]:
    cleaned = [t.strip() for t in terms if t.strip()]
    if not cleaned:
        return {}

    uniq: list[str] = []
    seen: set[str] = set()
    for t in cleaned:
        if t not in seen:
            seen.add(t)
            uniq.append(t)

    human = _TRANSLATION_USER_TEMPLATE.format(
        terms="\n".join(uniq),
        schema=llm_translation_batch_json_schema_text(),
    )
    messages = [SystemMessage(content=_TRANSLATION_SYSTEM_PROMPT), HumanMessage(content=human)]
    try:
        raw = _retry_invoke(model, messages)
    except Exception:
        logger.exception("Bedrock invoke failed permanently for translation")
        return {}
    text = _message_content_to_text(raw.content)
    text = _FENCE.sub("", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        recovered = _extract_first_json_object(text)
        if recovered is None:
            logger.error("Translation JSON parse failed; snippet=%s", text[:200])
            return {}
        try:
            data = json.loads(recovered)
        except json.JSONDecodeError:
            logger.error("Translation JSON recovery parse failed; snippet=%s", recovered[:200])
            return {}
    try:
        batch = LlmTranslationBatch.model_validate(data)
    except Exception:
        logger.exception("validate translation JSON failed")
        return {}

    out: dict[str, str] = {}
    for item in batch.translations:
        if item.simplified and item.english:
            out[item.simplified] = item.english
    return out


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
