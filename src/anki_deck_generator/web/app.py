"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from anki_deck_generator.config.settings import ServerSettings
from anki_deck_generator.web.routes import health, sync


def create_app(*, server_settings: ServerSettings | None = None) -> FastAPI:
    settings = server_settings or ServerSettings()
    app = FastAPI(title="Anki Notes Pipeline", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(sync.router)
    return app
