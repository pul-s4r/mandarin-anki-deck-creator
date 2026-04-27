from __future__ import annotations

import importlib
import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

from anki_deck_generator.cli import main
from anki_deck_generator.errors import IntegrationError
from anki_deck_generator.integrations.registry import available_providers, get_provider

importlib.import_module("anki_deck_generator.integrations.echo")


def test_get_provider_unknown_raises_integration_error() -> None:
    with pytest.raises(IntegrationError, match="unknown integration provider"):
        get_provider("not-a-thing")


def test_echo_provider_registered() -> None:
    assert "echo" in available_providers()
    p = get_provider("echo")
    assert p.name == "echo"
    p.authenticate({})
    r = p.import_documents()
    assert r.source_description
    assert len(r.documents) == 1
    assert r.documents[0].format == "txt"


def test_main_import_list_providers() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["import", "--list-providers"])
    assert code == 0
    names = {line.strip() for line in buf.getvalue().splitlines() if line.strip()}
    assert names == {"echo"}


def test_main_import_echo() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["import", "echo"])
    assert code == 0
    out = buf.getvalue()
    assert "echo test provider" in out
    assert "echo.txt" in out


def test_main_import_unknown_provider_exits_1() -> None:
    err = io.StringIO()
    with redirect_stderr(err):
        code = main(["import", "missing-provider"])
    assert code == 1
    assert "unknown integration provider" in err.getvalue()
