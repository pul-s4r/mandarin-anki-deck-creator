"""FastAPI dependency providers."""

from __future__ import annotations

import hmac
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status

from anki_deck_generator.config.settings import ServerSettings, Settings
from anki_deck_generator.dictionary.index import DictionaryIndex
from anki_deck_generator.dictionary.source import FileLineDictionarySource
from anki_deck_generator.state import StateStore, get_store

@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_server_settings() -> ServerSettings:
    return ServerSettings()


@lru_cache
def get_dictionary_index(settings: Annotated[Settings, Depends(get_settings)]) -> DictionaryIndex | None:
    if settings.cedict_path and settings.cedict_path.is_file():
        return DictionaryIndex.from_source(FileLineDictionarySource(settings.cedict_path))
    return None


def get_state_store(settings: Annotated[Settings, Depends(get_settings)]) -> StateStore:
    store = get_store(settings)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="State backend is not configured (set ANKI_PIPELINE_STATE_BACKEND=sqlite or dynamodb)",
        )
    return store


def require_register_secret(
    body_secret: str,
    server_settings: Annotated[ServerSettings, Depends(get_server_settings)],
) -> None:
    expected = server_settings.agent_register_secret
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent registration is disabled (set ANKI_SERVER_AGENT_REGISTER_SECRET)",
        )
    if not hmac.compare_digest(body_secret, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid register secret")
