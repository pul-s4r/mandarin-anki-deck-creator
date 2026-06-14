"""Pydantic models for HTTP API request/response bodies."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class VocabularyRowResponse(BaseModel):
    key: int
    simplified: str
    traditional: str = ""
    pinyin: str = ""
    meaning: str = ""
    part_of_speech: str = ""
    usage_notes: str = ""
    sentence_simplified: str = ""


class PipelineStatsResponse(BaseModel):
    block_count: int
    chunk_count: int
    raw_card_count: int
    deduped_card_count: int
    enriched_count: int
    llm_translation_fallback_count: int
    decomposition_fallback_count: int
    sentence_link_count: int


class SyncRunResponse(BaseModel):
    rows: list[VocabularyRowResponse]
    stats: PipelineStatsResponse
    sentence_link_count: int = Field(description="Number of sentence links produced")


class ErrorResponse(BaseModel):
    detail: str
