"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from anki_deck_generator.web.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()
