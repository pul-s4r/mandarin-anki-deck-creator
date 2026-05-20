from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, field_validator


class LlmVocabularyItem(BaseModel):
    simplified: str = Field(description="Simplified Chinese headword")
    traditional: str = Field(default="", description="Traditional form if known")
    pinyin: str = Field(default="", description="Hanyu Pinyin with tone marks when possible")
    meaning: str = Field(default="", description="English gloss")
    part_of_speech: str = Field(default="", description="Part(s) of speech, semicolon-separated")
    usage_notes: str = Field(default="", description="Grammar or usage notes")

    @field_validator("part_of_speech", mode="before")
    @classmethod
    def _coerce_pos(cls, v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, list):
            return "; ".join(str(x).strip() for x in v if str(x).strip())
        return str(v).strip()

    @field_validator(
        "simplified",
        "traditional",
        "pinyin",
        "meaning",
        "usage_notes",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()


class LlmVocabularyResult(BaseModel):
    cards: list[LlmVocabularyItem] = Field(
        default_factory=list,
        description="One object per distinct vocabulary item, phrase, or grammar point",
    )


def llm_vocabulary_response_json_schema_text() -> str:
    """JSON Schema for LLM responses; kept in sync with validation via LlmVocabularyResult."""
    return json.dumps(LlmVocabularyResult.model_json_schema(), ensure_ascii=False, indent=2)


class LlmTranslationItem(BaseModel):
    simplified: str = Field(description="Simplified Chinese term exactly as given in the input list")
    english: str = Field(description="Concise English gloss suitable for a flashcard")

    @field_validator("simplified", "english", mode="before")
    @classmethod
    def _strip_strings(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()


class LlmTranslationBatch(BaseModel):
    translations: list[LlmTranslationItem] = Field(
        default_factory=list,
        description="One object per input term, in any order",
    )


def llm_translation_batch_json_schema_text() -> str:
    return json.dumps(LlmTranslationBatch.model_json_schema(), ensure_ascii=False, indent=2)
