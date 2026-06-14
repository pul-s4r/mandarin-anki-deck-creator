"""AnkiWeb pull-agent HTTP API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from anki_deck_generator.config.settings import ServerSettings
from anki_deck_generator.errors import StateError
from anki_deck_generator.state.store import StateStore
from anki_deck_generator.sync.ankiweb_agent_service import (
    AckItemResult,
    ack_batch,
    authenticate_agent,
    issue_pending_batch,
    register_agent,
)
from anki_deck_generator.web.dependencies import (
    get_server_settings,
    get_state_store,
    require_register_secret,
)
from anki_deck_generator.web.schemas import (
    AckRequest,
    AckResponse,
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentRevokeRequest,
    PendingAnkiNote,
    PendingBatchItemResponse,
    PendingBatchResponse,
)

router = APIRouter(prefix="/api/ankiweb", tags=["ankiweb-agent"])


def _parse_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    return authorization[7:].strip()


def _auth_agent(
    store: StateStore,
    *,
    agent_id: str,
    authorization: str | None,
) -> None:
    token = _parse_bearer(authorization)
    try:
        authenticate_agent(store, user_id="default", agent_id=agent_id, token=token)
    except StateError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.post("/agent/register", response_model=AgentRegisterResponse)
def agent_register(
    body: AgentRegisterRequest,
    store: Annotated[StateStore, Depends(get_state_store)],
    server_settings: Annotated[ServerSettings, Depends(get_server_settings)],
) -> AgentRegisterResponse:
    require_register_secret(body.register_secret, server_settings)
    _, token = register_agent(store, user_id="default", agent_id=body.agent_id)
    return AgentRegisterResponse(agent_id=body.agent_id, token=token)


@router.post("/agent/revoke", status_code=status.HTTP_204_NO_CONTENT)
def agent_revoke(
    body: AgentRevokeRequest,
    store: Annotated[StateStore, Depends(get_state_store)],
    server_settings: Annotated[ServerSettings, Depends(get_server_settings)],
    register_secret: Annotated[str, Query(alias="register_secret")] = "",
) -> None:
    require_register_secret(register_secret or "", server_settings)
    store.revoke_agent(body.agent_id, user_id="default")


@router.get("/pending", response_model=PendingBatchResponse)
def get_pending(
    agent_id: Annotated[str, Query()],
    cursor: Annotated[str | None, Query()] = None,
    authorization: Annotated[str | None, Header()] = None,
    store: Annotated[StateStore, Depends(get_state_store)] = None,  # type: ignore[assignment]
    server_settings: Annotated[ServerSettings, Depends(get_server_settings)] = None,  # type: ignore[assignment]
) -> PendingBatchResponse:
    _auth_agent(store, agent_id=agent_id, authorization=authorization)
    batch = issue_pending_batch(
        store,
        user_id="default",
        agent_id=agent_id,
        cursor_raw=cursor,
        limit=server_settings.ankiweb_pending_batch_size,
        deck_name=server_settings.ankiweb_deck_name,
        model_name=server_settings.ankiweb_model_name,
    )
    items = [
        PendingBatchItemResponse(
            op=item.op,
            card_id=item.card_id,
            anki=PendingAnkiNote.model_validate(item.anki),
            base_fields=item.base_fields,
        )
        for item in batch.items
    ]
    return PendingBatchResponse(cursor=batch.cursor, batch_id=batch.batch_id, items=items)


@router.post("/ack", response_model=AckResponse)
def post_ack(
    body: AckRequest,
    authorization: Annotated[str | None, Header()] = None,
    store: Annotated[StateStore, Depends(get_state_store)] = None,  # type: ignore[assignment]
) -> AckResponse:
    _auth_agent(store, agent_id=body.agent_id, authorization=authorization)
    results = [
        AckItemResult(
            card_id=item.card_id,
            op=item.op,
            status=item.status,
            anki_note_id=item.anki_note_id,
            error=item.error,
            conflict=item.conflict.model_dump() if item.conflict else None,
            applied_fields=item.applied_fields,
        )
        for item in body.results
    ]
    try:
        summary = ack_batch(
            store,
            user_id="default",
            agent_id=body.agent_id,
            batch_id=body.batch_id,
            results=results,
            sync_requested=body.sync_requested,
            sync_status=body.sync_status,
            duration_ms=body.duration_ms,
        )
    except StateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AckResponse(
        created=summary.created,
        updated=summary.updated,
        unchanged=summary.unchanged,
        skipped=summary.skipped,
        conflicts=summary.conflicts,
        errors=summary.errors,
    )
