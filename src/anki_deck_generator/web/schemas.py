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


class SyncRunPersistenceResponse(BaseModel):
    run_id: str
    source_id: str
    cards_created: int = 0
    cards_updated: int = 0
    cards_unchanged: int = 0


class SyncRunResponse(BaseModel):
    rows: list[VocabularyRowResponse]
    stats: PipelineStatsResponse
    sentence_link_count: int = Field(description="Number of sentence links produced")
    persistence: SyncRunPersistenceResponse | None = None


class ErrorResponse(BaseModel):
    detail: str


class AgentRegisterRequest(BaseModel):
    agent_id: str
    register_secret: str = ""


class AgentRegisterResponse(BaseModel):
    agent_id: str
    token: str


class AgentRevokeRequest(BaseModel):
    agent_id: str


class PendingAnkiNote(BaseModel):
    deckName: str
    modelName: str
    fields: dict[str, str]
    tags: list[str]
    options: dict[str, object] = Field(default_factory=dict)


class PendingBatchItemResponse(BaseModel):
    op: str
    card_id: str
    anki: PendingAnkiNote
    base_fields: dict[str, str] | None = None


class PendingBatchResponse(BaseModel):
    cursor: str
    batch_id: str
    items: list[PendingBatchItemResponse]


class AckConflictResponse(BaseModel):
    fields: list[str]
    chosen: str


class AckItemRequest(BaseModel):
    card_id: str
    op: str
    status: str
    anki_note_id: int | None = None
    error: str | None = None
    conflict: AckConflictResponse | None = None
    applied_fields: dict[str, str] | None = None


class AckRequest(BaseModel):
    batch_id: str
    agent_id: str
    results: list[AckItemRequest]
    sync_requested: bool = False
    sync_status: str = ""
    duration_ms: int = 0


class AckResponse(BaseModel):
    status: str = "ok"
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    conflicts: int = 0
    errors: int = 0


class AnkiWebExportBlock(BaseModel):
    agent_id: str = ""
    batch_id: str = ""
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    conflicts: int = 0
    errors: int = 0
    sync_requested: bool = False
    sync_status: str = ""
    duration_ms: int = 0
    exporter: str = "ankiweb"


class SyncRunDetailResponse(BaseModel):
    run_id: str
    trigger: str
    started_at: str | None = None
    finished_at: str | None = None
    sync_report: dict[str, object] = Field(default_factory=dict)
    exports_ankiweb: list[AnkiWebExportBlock] = Field(default_factory=list)
