from __future__ import annotations

import threading

import boto3
import pytest
from moto import mock_aws

from anki_deck_generator.errors import StateError
from anki_deck_generator.state.dynamo_store import DynamoStateStore
from anki_deck_generator.state.dynamo_table import DEFAULT_TABLE_NAME, dynamo_table_definition
from anki_deck_generator.state.records import DriveChannelRecord
from tests.conformance_state_store import StateStoreConformanceTests


@pytest.fixture
def store() -> DynamoStateStore:
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(**dynamo_table_definition(table_name=DEFAULT_TABLE_NAME))
        yield DynamoStateStore(table_name=DEFAULT_TABLE_NAME, dynamodb_resource=resource)


class TestDynamoStateStoreConformance(StateStoreConformanceTests):
    pass


def test_advance_drive_channel_token_dynamo_collision(store: DynamoStateStore) -> None:
    store.upsert_drive_channel(
        DriveChannelRecord(channel_id="ch1", page_token="tok-a", resource_id="res1")
    )
    store.advance_drive_channel_token("ch1", expected_token="tok-a", new_token="tok-b")

    errors: list[BaseException] = []

    def attempt(expected: str, new: str) -> None:
        try:
            store.advance_drive_channel_token("ch1", expected_token=expected, new_token=new)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=attempt, args=("tok-b", "tok-c")),
        threading.Thread(target=attempt, args=("tok-b", "tok-d")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    state_errors = [exc for exc in errors if isinstance(exc, StateError)]
    assert len(state_errors) == 1
    got = store.get_drive_channel("ch1")
    assert got is not None and got.page_token in {"tok-c", "tok-d"}
