from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.state import get_store
from anki_deck_generator.state.dynamo_store import DynamoStateStore
from anki_deck_generator.state.dynamo_table import DEFAULT_TABLE_NAME, dynamo_table_definition
from anki_deck_generator.state.sqlite_store import SqliteStateStore


def test_get_store_none() -> None:
    s = Settings(state_backend="none")
    assert get_store(s) is None


def test_get_store_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    s = Settings(state_backend="sqlite", state_db_path=db)
    st = get_store(s)
    assert isinstance(st, SqliteStateStore)
    st.init_schema()
    st.close()


def test_get_store_dynamodb() -> None:
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(**dynamo_table_definition(table_name=DEFAULT_TABLE_NAME))
        s = Settings(state_backend="dynamodb", dynamodb_table_name=DEFAULT_TABLE_NAME)
        with patch("boto3.resource", return_value=resource):
            st = get_store(s)
        assert isinstance(st, DynamoStateStore)
