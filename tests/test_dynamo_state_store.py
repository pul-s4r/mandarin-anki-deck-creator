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


# ─────────────── M8: DriveChannelRecord extended fields ─────────────── #


def test_drive_channel_m8_fields_dynamo(store: DynamoStateStore) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    rec = DriveChannelRecord(
        channel_id="m8-chan",
        resource_id="res-m8",
        page_token="tok-m8",
        source_set_name="set-a",
        channel_token="my-secret",
        last_advanced_at=now,
    )
    store.upsert_drive_channel(rec)
    got = store.get_drive_channel("m8-chan")
    assert got is not None
    assert got.source_set_name == "set-a"
    assert got.channel_token == "my-secret"


# ─────────────── M8: PendingEditRecord ──────────────────────────────── #


def test_pending_edit_upsert_and_list_dynamo(store: DynamoStateStore) -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    past = now - timedelta(hours=1)

    # Insert a ready edit.
    rec = store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="file-1",
        now=past,
        quiet_seconds=10,
        max_delay_seconds=7200,
    )
    assert rec.file_id == "file-1"

    ready = store.list_ready_pending_edits(user_id="default", now=now)
    assert any(r.file_id == "file-1" for r in ready)


def test_pending_edit_clear_guarded_dynamo(store: DynamoStateStore) -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="file-2",
        now=now,
        quiet_seconds=10,
        max_delay_seconds=7200,
    )
    cleared = store.clear_pending_edit(
        user_id="default",
        source_set_name="my-set",
        file_id="file-2",
        if_last_seen_before=now + timedelta(seconds=5),
    )
    assert cleared is True
    assert store.get_pending_edit(user_id="default", source_set_name="my-set", file_id="file-2") is None


def test_pending_edit_force_dynamo(store: DynamoStateStore) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    store.upsert_pending_edit_debounced(
        user_id="default",
        source_set_name="my-set",
        file_id="file-3",
        now=now,
        quiet_seconds=3600,
        max_delay_seconds=7200,
    )
    store.force_pending_edit(user_id="default", source_set_name="my-set", file_id="file-3")
    ready = store.list_ready_pending_edits(user_id="default", now=now)
    assert any(r.file_id == "file-3" for r in ready)
