"""Thin synchronous HTTP JSON-RPC client for AnkiConnect v6."""

from __future__ import annotations

import argparse
import json
from typing import Any, Self

import httpx

from anki_deck_generator.errors import AnkiConnectError

_ANKI_CONNECT_API_VERSION = 6


class AnkiConnectClient:
    """POST JSON-RPC to AnkiConnect (default ``http://127.0.0.1:8765``)."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8765",
        api_key: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._url = base_url.rstrip("/") + "/"
        self._api_key = api_key
        self._http = httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _invoke(self, action: str, params: dict[str, Any] | None = None) -> Any:
        body: dict[str, Any] = {"action": action, "version": _ANKI_CONNECT_API_VERSION}
        if params:
            body["params"] = params
        if self._api_key:
            body["key"] = self._api_key
        try:
            resp = self._http.post(self._url, json=body)
        except httpx.ConnectError as exc:
            raise AnkiConnectError(
                "Cannot reach AnkiConnect — is Anki running with the AnkiConnect add-on enabled?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise AnkiConnectError("AnkiConnect request timed out.") from exc
        if resp.status_code != 200:
            raise AnkiConnectError(f"AnkiConnect HTTP {resp.status_code}: {resp.text[:512]!r}")
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise AnkiConnectError(f"AnkiConnect returned non-JSON: {resp.text[:256]!r}") from exc
        if not isinstance(payload, dict):
            raise AnkiConnectError(f"AnkiConnect returned unexpected JSON type: {type(payload).__name__}")
        err = payload.get("error")
        if err is not None:
            raise AnkiConnectError(str(err))
        return payload.get("result")

    def version(self) -> int:
        return int(self._invoke("version"))

    def request_permission(self) -> dict[str, Any]:
        raw = self._invoke("requestPermission")
        return raw if isinstance(raw, dict) else {}

    def deck_names(self) -> list[str]:
        raw = self._invoke("deckNames")
        return list(raw) if isinstance(raw, list) else []

    def create_deck(self, deck_name: str) -> Any:
        return self._invoke("createDeck", {"deck": deck_name})

    def model_names(self) -> list[str]:
        raw = self._invoke("modelNames")
        return list(raw) if isinstance(raw, list) else []

    def model_field_names(self, model_name: str) -> list[str]:
        raw = self._invoke("modelFieldNames", {"modelName": model_name})
        return list(raw) if isinstance(raw, list) else []

    def create_model(self, model_spec: dict[str, Any]) -> Any:
        return self._invoke("createModel", dict(model_spec))

    def can_add_notes_with_error_detail(self, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raw = self._invoke("canAddNotesWithErrorDetail", {"notes": notes})
        return list(raw) if isinstance(raw, list) else []

    def add_notes(self, notes: list[dict[str, Any]]) -> list[Any]:
        raw = self._invoke("addNotes", {"notes": notes})
        return list(raw) if isinstance(raw, list) else []

    def update_note(self, *, note: dict[str, Any]) -> Any:
        return self._invoke("updateNote", {"note": dict(note)})

    def update_note_fields(self, *, note_id: int, fields: dict[str, str]) -> Any:
        return self._invoke("updateNoteFields", {"id": note_id, "fields": dict(fields)})

    def update_note_tags(self, *, note_id: int, tags: str) -> Any:
        return self._invoke("updateNoteTags", {"note": note_id, "tags": tags})

    def find_notes(self, query: str) -> list[int]:
        raw = self._invoke("findNotes", {"query": query})
        return [int(x) for x in raw] if isinstance(raw, list) else []

    def notes_info(self, note_ids: list[int]) -> list[dict[str, Any]]:
        raw = self._invoke("notesInfo", {"notes": note_ids})
        return list(raw) if isinstance(raw, list) else []

    def add_tags(self, *, note_ids: list[int], tags: str) -> Any:
        return self._invoke("addTags", {"notes": note_ids, "tags": tags})

    def remove_tags(self, *, note_ids: list[int], tags: str) -> Any:
        return self._invoke("removeTags", {"notes": note_ids, "tags": tags})

    def sync(self) -> Any:
        return self._invoke("sync")

    def multi(self, actions: list[dict[str, Any]]) -> list[Any]:
        wrapped = [{"action": a["action"], "version": _ANKI_CONNECT_API_VERSION, **({"params": a["params"]} if "params" in a else {})} for a in actions]
        raw = self._invoke("multi", {"actions": wrapped})
        return list(raw) if isinstance(raw, list) else []


def _cli_main() -> None:
    parser = argparse.ArgumentParser(description="Minimal AnkiConnect smoke probe.")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--action", default="version")
    args = parser.parse_args()
    client = AnkiConnectClient(base_url=args.url)
    try:
        if args.action == "version":
            print(client.version())
        else:
            raise SystemExit(f"unsupported action {args.action!r} (only version)")
    finally:
        client.close()


if __name__ == "__main__":
    _cli_main()
