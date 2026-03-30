from __future__ import annotations

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
    cards: list[LlmVocabularyItem] = Field(default_factory=list)
