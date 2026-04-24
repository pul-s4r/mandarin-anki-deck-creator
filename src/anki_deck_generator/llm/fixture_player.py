from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage

from anki_deck_generator.errors import LlmError
from anki_deck_generator.llm.schemas import LlmVocabularyItem

logger = logging.getLogger(__name__)


def chunk_content_key(chunk_text: str) -> str:
    """SHA-256 hex digest of UTF-8 chunk text (must match recording / baselines)."""
    return hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()


@dataclass
class LlmFixtureData:
    chunks: dict[str, list[dict[str, Any]]]
    translations: dict[str, str]

    @classmethod
    def load(cls, path: Path) -> LlmFixtureData:
        raw = json.loads(path.read_text(encoding="utf-8"))
        chunks = {str(k): list(v) for k, v in (raw.get("chunks") or {}).items()}
        translations = {
            str(k): str(v) for k, v in (raw.get("translations") or {}).items()
        }
        return cls(chunks=chunks, translations=translations)


class FixtureLlmModel:
    """
    Deterministic stand-in for ChatBedrockConverse when ANKI_PIPELINE_LLM_FIXTURE_PATH is set.

    `extract_vocabulary_from_chunk` / `translate_simplified_terms` branch on this type instead
    of calling `.invoke()` so we do not need to mimic Bedrock message shapes.
    """

    def __init__(self, data: LlmFixtureData) -> None:
        self._data = data

    @classmethod
    def from_path(cls, path: Path) -> FixtureLlmModel:
        return cls(LlmFixtureData.load(path))

    def vocabulary_for_chunk(self, chunk_text: str) -> list[LlmVocabularyItem]:
        key = chunk_content_key(chunk_text)
        raw_cards = self._data.chunks.get(key)
        if raw_cards is None:
            snippet = chunk_text[:120].replace("\n", "\\n")
            raise LlmError(
                f"LLM fixture missing chunk key {key!r} (snippet={snippet!r}). "
                "Regenerate tests/baselines/llm_mock.json with tests/baselines/record.py."
            )
        return [LlmVocabularyItem.model_validate(c) for c in raw_cards]

    def translations_for_terms(self, terms: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for t in terms:
            s = t.strip()
            if not s:
                continue
            if s in self._data.translations:
                out[s] = self._data.translations[s]
            else:
                logger.warning("LLM fixture missing translation for term %r", s)
        return out

    def invoke(self, _messages: list[BaseMessage]) -> Any:
        """Not used when fixture path is set; present so accidental use fails loudly."""
        raise RuntimeError(
            "FixtureLlmModel.invoke should not be called; use vocabulary_for_chunk / translations_for_terms."
        )
