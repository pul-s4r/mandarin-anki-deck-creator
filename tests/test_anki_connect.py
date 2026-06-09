"""Tests for AnkiConnect HTTP JSON-RPC client (stubbed httpx)."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from anki_deck_generator.errors import AnkiConnectError
from anki_deck_generator.export.ankiweb.anki_connect import AnkiConnectClient


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_http = MagicMock()

    def _factory(**_kwargs: object) -> MagicMock:
        return mock_http

    monkeypatch.setattr(
        "anki_deck_generator.export.ankiweb.anki_connect.httpx.Client",
        _factory,
    )
    return mock_http


def _ok_json(payload: dict[str, object]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = ""
    resp.json.return_value = payload
    return resp


def test_version(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": 6, "error": None})
    client = AnkiConnectClient()
    assert client.version() == 6
    _, kw = mock_http.post.call_args
    assert kw["json"]["action"] == "version"
    assert kw["json"]["version"] == 6


def test_request_permission(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": {"permission": "granted"}, "error": None})
    client = AnkiConnectClient()
    assert client.request_permission() == {"permission": "granted"}
    assert mock_http.post.call_args.kwargs["json"]["action"] == "requestPermission"


def test_deck_names(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": ["Default"], "error": None})
    client = AnkiConnectClient()
    assert client.deck_names() == ["Default"]
    assert mock_http.post.call_args.kwargs["json"]["action"] == "deckNames"


def test_create_deck(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": 10498293489, "error": None})
    client = AnkiConnectClient()
    assert client.create_deck("Chinese::NEW") == 10498293489
    body = mock_http.post.call_args.kwargs["json"]
    assert body["action"] == "createDeck"
    assert body["params"] == {"deck": "Chinese::NEW"}


def test_model_names(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": ["Basic"], "error": None})
    client = AnkiConnectClient()
    assert client.model_names() == ["Basic"]
    assert mock_http.post.call_args.kwargs["json"]["action"] == "modelNames"


def test_model_field_names(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": ["Front"], "error": None})
    client = AnkiConnectClient()
    assert client.model_field_names("Basic") == ["Front"]
    body = mock_http.post.call_args.kwargs["json"]
    assert body["params"]["modelName"] == "Basic"


def test_create_model(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": None, "error": None})
    client = AnkiConnectClient()
    spec = {"modelName": "M", "inOrderFields": ["a"], "css": "", "cardTemplates": [{}]}
    assert client.create_model(spec) is None
    body = mock_http.post.call_args.kwargs["json"]
    assert body["action"] == "createModel"
    assert body["params"] == spec


def test_can_add_notes_with_error_detail(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": [{"canAdd": True}], "error": None})
    client = AnkiConnectClient()
    notes = [{"deckName": "d", "modelName": "m", "fields": {}, "tags": []}]
    assert client.can_add_notes_with_error_detail(notes) == [{"canAdd": True}]
    assert mock_http.post.call_args.kwargs["json"]["params"]["notes"] == notes


def test_add_notes(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": [123, None], "error": None})
    client = AnkiConnectClient()
    notes = [{"deckName": "d", "modelName": "m", "fields": {}, "tags": []}]
    assert client.add_notes(notes) == [123, None]


def test_update_note(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": None, "error": None})
    client = AnkiConnectClient()
    note = {"id": 1, "fields": {"k": "v"}, "tags": ["t"]}
    client.update_note(note=note)
    body = mock_http.post.call_args.kwargs["json"]
    assert body["action"] == "updateNote"
    assert body["params"]["note"] == note


def test_update_note_fields(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": None, "error": None})
    client = AnkiConnectClient()
    client.update_note_fields(note_id=9, fields={"a": "b"})
    body = mock_http.post.call_args.kwargs["json"]
    assert body["params"] == {"id": 9, "fields": {"a": "b"}}


def test_update_note_tags(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": None, "error": None})
    client = AnkiConnectClient()
    client.update_note_tags(note_id=7, tags="a b")
    body = mock_http.post.call_args.kwargs["json"]
    assert body["params"] == {"note": 7, "tags": "a b"}


def test_find_notes(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": ["1483959289817"], "error": None})
    client = AnkiConnectClient()
    ids = client.find_notes('tag:"ext_id:x"')
    assert ids == [1483959289817]


def test_notes_info(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": [{"noteId": 1}], "error": None})
    client = AnkiConnectClient()
    assert client.notes_info([1]) == [{"noteId": 1}]


def test_add_tags(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": None, "error": None})
    client = AnkiConnectClient()
    client.add_tags(note_ids=[1], tags="x")
    body = mock_http.post.call_args.kwargs["json"]
    assert body["params"] == {"notes": [1], "tags": "x"}


def test_remove_tags(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": None, "error": None})
    client = AnkiConnectClient()
    client.remove_tags(note_ids=[2], tags="y")
    body = mock_http.post.call_args.kwargs["json"]
    assert body["params"] == {"notes": [2], "tags": "y"}


def test_sync(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": "foo", "error": None})
    client = AnkiConnectClient()
    assert client.sync() == "foo"


def test_multi(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": [["A"], ["B"]], "error": None})
    client = AnkiConnectClient()
    actions = [
        {"action": "deckNames"},
        {"action": "findNotes", "params": {"query": "deck:current"}},
    ]
    assert client.multi(actions) == [["A"], ["B"]]
    body = mock_http.post.call_args.kwargs["json"]
    inner = body["params"]["actions"]
    assert inner[0] == {"action": "deckNames", "version": 6}
    assert inner[1] == {"action": "findNotes", "version": 6, "params": {"query": "deck:current"}}


def test_response_error_raises(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": None, "error": "denied"})
    client = AnkiConnectClient()
    with pytest.raises(AnkiConnectError, match="denied"):
        client.version()


def test_connect_error_raises(mock_http: MagicMock) -> None:
    mock_http.post.side_effect = httpx.ConnectError("refused", request=None)
    client = AnkiConnectClient()
    with pytest.raises(AnkiConnectError, match="Cannot reach AnkiConnect"):
        client.version()


def test_non_200_raises(mock_http: MagicMock) -> None:
    resp = MagicMock()
    resp.status_code = 500
    resp.text = "oops"
    mock_http.post.return_value = resp
    client = AnkiConnectClient()
    with pytest.raises(AnkiConnectError, match="HTTP 500"):
        client.version()


def test_api_key_included_when_set(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": 6, "error": None})
    client = AnkiConnectClient(api_key="secret")
    client.version()
    assert mock_http.post.call_args.kwargs["json"]["key"] == "secret"


def test_api_key_omitted_when_none(mock_http: MagicMock) -> None:
    mock_http.post.return_value = _ok_json({"result": 6, "error": None})
    client = AnkiConnectClient(api_key=None)
    client.version()
    assert "key" not in mock_http.post.call_args.kwargs["json"]
