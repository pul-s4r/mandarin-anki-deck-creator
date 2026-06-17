"""Incremental sync / pipeline run API."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from anki_deck_generator.config.settings import ServerSettings, Settings
from anki_deck_generator.errors import AnkiPipelineError, IngestError
from anki_deck_generator.ingest.router import extract_text_from_bytes
from anki_deck_generator.pipeline import run_pipeline_from_text
from anki_deck_generator.pipeline_types import PipelineResult
from anki_deck_generator.state.store import StateStore
from anki_deck_generator.web.dependencies import get_server_settings, get_settings, get_state_store
from anki_deck_generator.web.schemas import (
    PipelineStatsResponse,
    SyncRunResponse,
    VocabularyRowResponse,
)

router = APIRouter(prefix="/api/sync", tags=["sync"])


def _format_from_filename(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".docx":
        return "docx"
    raise IngestError(f"Unsupported input type: {suffix} ({filename})")


async def _read_upload_limited(upload: UploadFile, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Upload exceeds max size of {max_bytes} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _pipeline_result_to_response(result: PipelineResult) -> SyncRunResponse:
    return SyncRunResponse(
        rows=[
            VocabularyRowResponse(
                key=row.key,
                simplified=row.simplified,
                traditional=row.traditional,
                pinyin=row.pinyin,
                meaning=row.meaning,
                part_of_speech=row.part_of_speech,
                usage_notes=row.usage_notes,
                sentence_simplified=row.sentence_simplified,
            )
            for row in result.rows
        ],
        stats=PipelineStatsResponse(
            block_count=result.stats.block_count,
            chunk_count=result.stats.chunk_count,
            raw_card_count=result.stats.raw_card_count,
            deduped_card_count=result.stats.deduped_card_count,
            enriched_count=result.stats.enriched_count,
            llm_translation_fallback_count=result.stats.llm_translation_fallback_count,
            decomposition_fallback_count=result.stats.decomposition_fallback_count,
            sentence_link_count=result.stats.sentence_link_count,
        ),
        sentence_link_count=len(result.sentence_links),
    )


@router.post("/run", response_model=SyncRunResponse)
async def sync_run(
    file: Annotated[UploadFile, File()],
    _store: Annotated[StateStore, Depends(get_state_store)],
    settings: Annotated[Settings, Depends(get_settings)],
    server_settings: Annotated[ServerSettings, Depends(get_server_settings)],
    skip_lines_filter: Annotated[bool | None, Form()] = None,
    enable_sentences: Annotated[bool | None, Form()] = None,
) -> SyncRunResponse:
    """Upload a study-notes file and run the vocabulary pipeline (blocking until complete)."""
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename")

    max_bytes = server_settings.max_upload_size_mb * 1024 * 1024
    try:
        data = await _read_upload_limited(file, max_bytes=max_bytes)
        fmt = _format_from_filename(file.filename)
        text = extract_text_from_bytes(data, format=fmt)
    except IngestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    run_settings = settings.model_copy(deep=True)
    if skip_lines_filter is not None:
        run_settings.skip_lines_filter = skip_lines_filter
    if enable_sentences is not None:
        run_settings.enable_sentences = enable_sentences

    def run_pipeline() -> PipelineResult:
        return run_pipeline_from_text(text, run_settings)

    try:
        result = await asyncio.to_thread(run_pipeline)
    except AnkiPipelineError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return _pipeline_result_to_response(result)
