"""DynamoDB-backed StateStore (single-table design)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from anki_deck_generator.errors import StateError
from anki_deck_generator.state.card_compare import ankiweb_meta_matches_stored, dt_iso, normalize_stored_anki_fields
from anki_deck_generator.state.dynamo_table import CARD_BY_KEY_INDEX
from anki_deck_generator.state.records import (
    AgentRecord,
    CardRecord,
    CardUpsertResult,
    ChunkRecord,
    DriveChannelRecord,
    IssuedBatchRecord,
    PendingEditRecord,
    PendingSyncCursor,
    RunReportRecord,
    SourceRecord,
    compute_card_content_hash,
)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _source_keys(rec: SourceRecord) -> dict[str, str]:
    return {
        "pk": f"USER#{rec.user_id}",
        "sk": f"SRC#{rec.provider}#{rec.external_id}",
    }


def _chunk_keys(rec: ChunkRecord) -> dict[str, str]:
    return {
        "pk": f"SRC#{rec.source_id}",
        "sk": f"CHUNK#{rec.chunk_index:05d}",
    }


def _card_keys(card_id: str) -> dict[str, str]:
    return {"pk": f"CARD#{card_id}", "sk": "META"}


def _channel_keys(channel_id: str) -> dict[str, str]:
    return {"pk": f"CHAN#{channel_id}", "sk": "META"}


def _run_keys(rec: RunReportRecord) -> dict[str, str]:
    return {"pk": f"USER#{rec.user_id}", "sk": f"RUN#{rec.run_id}"}


def _agent_keys(user_id: str, agent_id: str) -> dict[str, str]:
    return {"pk": f"agent#{user_id}", "sk": agent_id}


def _cursor_keys(user_id: str, agent_id: str) -> dict[str, str]:
    return {"pk": f"sync_cursor#{user_id}", "sk": agent_id}


def _batch_keys(batch_id: str) -> dict[str, str]:
    return {"pk": f"BATCH#{batch_id}", "sk": "META"}


def _pending_edit_keys(user_id: str, source_set_name: str, file_id: str) -> dict[str, str]:
    return {"pk": f"PENDING#{user_id}#{source_set_name}", "sk": file_id}


def _item_to_source(item: dict[str, Any]) -> SourceRecord:
    return SourceRecord(
        source_id=str(item["source_id"]),
        provider=str(item["provider"]),
        external_id=str(item["external_id"]),
        revision_id=str(item.get("revision_id", "")),
        etag=str(item.get("etag", "")),
        content_sha256=str(item.get("content_sha256", "")),
        last_ingested_at=_parse_dt(item.get("last_ingested_at")),
        schema_version=int(item.get("schema_version", 1)),
        user_id=str(item.get("user_id", "default")),
    )


def _item_to_chunk(item: dict[str, Any]) -> ChunkRecord:
    raw_ids = item.get("llm_output_card_ids", "[]")
    if isinstance(raw_ids, str):
        card_ids = json.loads(raw_ids)
    else:
        card_ids = list(raw_ids)
    return ChunkRecord(
        source_id=str(item["source_id"]),
        chunk_index=int(item["chunk_index"]),
        chunk_sha256=str(item.get("chunk_sha256", "")),
        processed_at=_parse_dt(item.get("processed_at")),
        model_id=str(item.get("model_id", "")),
        llm_output_card_ids=[str(x) for x in card_ids],
        schema_version=int(item.get("schema_version", 1)),
        user_id=str(item.get("user_id", "default")),
    )


def _item_to_card(item: dict[str, Any]) -> CardRecord:
    note_id = item.get("ankiweb_note_id")
    if isinstance(note_id, Decimal):
        note_id = int(note_id)
    raw_fields = item.get("ankiweb_last_synced_fields")
    fields: dict[str, str] | None
    if raw_fields is None:
        fields = None
    else:
        normalized = normalize_stored_anki_fields(raw_fields if isinstance(raw_fields, str) else raw_fields)
        fields = normalized or None
    return CardRecord(
        card_id=str(item["card_id"]),
        simplified=str(item["simplified"]),
        traditional=str(item.get("traditional", "")),
        pinyin=str(item.get("pinyin", "")),
        meaning=str(item.get("meaning", "")),
        part_of_speech=str(item.get("part_of_speech", "")),
        usage_notes=str(item.get("usage_notes", "")),
        sentence_simplified=str(item.get("sentence_simplified", "")),
        first_seen_source_id=str(item.get("first_seen_source_id", "")),
        last_updated_at=_parse_dt(item.get("last_updated_at")),
        content_hash=str(item.get("content_hash", "")),
        schema_version=int(item.get("schema_version", 1)),
        user_id=str(item.get("user_id", "default")),
        ankiweb_note_id=note_id,
        ankiweb_last_synced_at=_parse_dt(item.get("ankiweb_last_synced_at")),
        ankiweb_last_synced_fields=fields,
    )


def _item_to_channel(item: dict[str, Any]) -> DriveChannelRecord:
    return DriveChannelRecord(
        channel_id=str(item["channel_id"]),
        resource_id=str(item.get("resource_id", "")),
        page_token=str(item.get("page_token", "")),
        expiration=_parse_dt(item.get("expiration")),
        schema_version=int(item.get("schema_version", 1)),
        user_id=str(item.get("user_id", "default")),
        source_set_name=str(item.get("source_set_name", "")),
        channel_token=str(item.get("channel_token", "")),
        last_advanced_at=_parse_dt(item.get("last_advanced_at")),
    )


def _item_to_pending_edit(item: dict[str, Any]) -> PendingEditRecord:
    return PendingEditRecord(
        user_id=str(item.get("user_id", "default")),
        source_set_name=str(item.get("source_set_name", "")),
        file_id=str(item["file_id"]),
        first_seen_at=_parse_dt(item.get("first_seen_at")),
        last_seen_at=_parse_dt(item.get("last_seen_at")),
        ready_at=_parse_dt(item.get("ready_at")),
        hard_deadline_at=_parse_dt(item.get("hard_deadline_at")),
        force_process=bool(item.get("force_process", False)),
        schema_version=int(item.get("schema_version", 1)),
    )


def _item_to_run(item: dict[str, Any]) -> RunReportRecord:
    return RunReportRecord(
        run_id=str(item["run_id"]),
        trigger=str(item.get("trigger", "manual")),
        started_at=_parse_dt(item.get("started_at")),
        finished_at=_parse_dt(item.get("finished_at")),
        sync_report_json=str(item.get("sync_report_json", "{}")),
        schema_version=int(item.get("schema_version", 1)),
        user_id=str(item.get("user_id", "default")),
    )


class DynamoStateStore:
    """Single-table DynamoDB StateStore implementation."""

    def __init__(self, *, table_name: str, dynamodb_resource: Any | None = None) -> None:
        import boto3

        self._table_name = table_name
        self._resource = dynamodb_resource or boto3.resource("dynamodb", region_name="us-east-1")
        self._table = self._resource.Table(table_name)

    def get_source_record(self, provider: str, external_id: str, *, user_id: str = "default") -> SourceRecord | None:
        response = self._table.get_item(
            Key={"pk": f"USER#{user_id}", "sk": f"SRC#{provider}#{external_id}"},
        )
        item = response.get("Item")
        if not item:
            return None
        return _item_to_source(item)

    def upsert_source_record(self, rec: SourceRecord) -> None:
        item = {
            **_source_keys(rec),
            "entity_type": "source",
            "source_id": rec.source_id,
            "provider": rec.provider,
            "external_id": rec.external_id,
            "revision_id": rec.revision_id,
            "etag": rec.etag,
            "content_sha256": rec.content_sha256,
            "last_ingested_at": dt_iso(rec.last_ingested_at),
            "schema_version": rec.schema_version,
            "user_id": rec.user_id,
        }
        self._table.put_item(Item=item)

    def get_processed_chunk(self, source_id: str, chunk_index: int) -> ChunkRecord | None:
        response = self._table.get_item(
            Key={"pk": f"SRC#{source_id}", "sk": f"CHUNK#{chunk_index:05d}"},
        )
        item = response.get("Item")
        if not item:
            return None
        return _item_to_chunk(item)

    def upsert_processed_chunk(self, rec: ChunkRecord) -> None:
        item = {
            **_chunk_keys(rec),
            "entity_type": "chunk",
            "source_id": rec.source_id,
            "chunk_index": rec.chunk_index,
            "chunk_sha256": rec.chunk_sha256,
            "processed_at": dt_iso(rec.processed_at),
            "model_id": rec.model_id,
            "llm_output_card_ids": json.dumps(rec.llm_output_card_ids),
            "schema_version": rec.schema_version,
            "user_id": rec.user_id,
        }
        self._table.put_item(Item=item)

    def _get_card_item_by_key(self, natural_key: str, *, user_id: str) -> dict[str, Any] | None:
        response = self._table.query(
            IndexName=CARD_BY_KEY_INDEX,
            KeyConditionExpression="user_id = :uid AND simplified = :simp",
            ExpressionAttributeValues={":uid": user_id, ":simp": natural_key},
            Limit=1,
        )
        items = response.get("Items", [])
        if not items:
            return None
        return items[0]

    def get_card_by_key(self, natural_key: str, *, user_id: str = "default") -> CardRecord | None:
        item = self._get_card_item_by_key(natural_key, user_id=user_id)
        if item is None:
            return None
        return _item_to_card(item)

    def get_card_by_id(self, card_id: str) -> CardRecord | None:
        response = self._table.get_item(Key=_card_keys(card_id))
        item = response.get("Item")
        if not item:
            return None
        return _item_to_card(item)

    def _put_card_item(self, rec: CardRecord) -> None:
        fields_json = json.dumps(rec.ankiweb_last_synced_fields) if rec.ankiweb_last_synced_fields else None
        item: dict[str, Any] = {
            **_card_keys(rec.card_id),
            "entity_type": "card",
            "card_id": rec.card_id,
            "user_id": rec.user_id,
            "simplified": rec.simplified,
            "traditional": rec.traditional,
            "pinyin": rec.pinyin,
            "meaning": rec.meaning,
            "part_of_speech": rec.part_of_speech,
            "usage_notes": rec.usage_notes,
            "sentence_simplified": rec.sentence_simplified,
            "first_seen_source_id": rec.first_seen_source_id,
            "last_updated_at": dt_iso(rec.last_updated_at),
            "content_hash": rec.content_hash,
            "schema_version": rec.schema_version,
            "ankiweb_note_id": rec.ankiweb_note_id,
            "ankiweb_last_synced_at": dt_iso(rec.ankiweb_last_synced_at),
            "ankiweb_last_synced_fields": fields_json,
        }
        self._table.put_item(Item=item)

    def upsert_card(self, rec: CardRecord) -> CardUpsertResult:
        content_hash = rec.content_hash or compute_card_content_hash(
            simplified=rec.simplified,
            traditional=rec.traditional,
            pinyin=rec.pinyin,
            meaning=rec.meaning,
            part_of_speech=rec.part_of_speech,
            usage_notes=rec.usage_notes,
        )
        existing = self._get_card_item_by_key(rec.simplified, user_id=rec.user_id)
        now_dt = rec.last_updated_at or datetime.now(UTC)
        if existing is None:
            card_id = rec.card_id or str(uuid.uuid4())
            self._put_card_item(
                CardRecord(
                    card_id=card_id,
                    simplified=rec.simplified,
                    traditional=rec.traditional,
                    pinyin=rec.pinyin,
                    meaning=rec.meaning,
                    part_of_speech=rec.part_of_speech,
                    usage_notes=rec.usage_notes,
                    sentence_simplified=rec.sentence_simplified,
                    first_seen_source_id=rec.first_seen_source_id,
                    last_updated_at=now_dt,
                    content_hash=content_hash,
                    schema_version=rec.schema_version,
                    user_id=rec.user_id,
                    ankiweb_note_id=rec.ankiweb_note_id,
                    ankiweb_last_synced_at=rec.ankiweb_last_synced_at,
                    ankiweb_last_synced_fields=rec.ankiweb_last_synced_fields,
                )
            )
            return CardUpsertResult.CREATED

        if (existing.get("content_hash") or "") == content_hash and ankiweb_meta_matches_stored(
            stored_note_id=existing.get("ankiweb_note_id"),
            stored_synced_at=_parse_dt(existing.get("ankiweb_last_synced_at")),
            stored_synced_fields=existing.get("ankiweb_last_synced_fields"),
            rec=rec,
        ):
            return CardUpsertResult.UNCHANGED

        card_id = str(existing["card_id"])
        self._put_card_item(
            CardRecord(
                card_id=card_id,
                simplified=rec.simplified,
                traditional=rec.traditional,
                pinyin=rec.pinyin,
                meaning=rec.meaning,
                part_of_speech=rec.part_of_speech,
                usage_notes=rec.usage_notes,
                sentence_simplified=rec.sentence_simplified,
                first_seen_source_id=rec.first_seen_source_id,
                last_updated_at=rec.last_updated_at or datetime.now(UTC),
                content_hash=content_hash,
                schema_version=rec.schema_version,
                user_id=rec.user_id,
                ankiweb_note_id=rec.ankiweb_note_id,
                ankiweb_last_synced_at=rec.ankiweb_last_synced_at,
                ankiweb_last_synced_fields=rec.ankiweb_last_synced_fields,
            )
        )
        return CardUpsertResult.UPDATED

    def iter_cards_changed_since(self, ts: datetime, *, user_id: str = "default") -> Iterable[CardRecord]:
        iso = dt_iso(ts)
        response = self._table.query(
            IndexName=CARD_BY_KEY_INDEX,
            KeyConditionExpression="user_id = :uid",
            ExpressionAttributeValues={":uid": user_id},
        )
        items = response.get("Items", [])
        while True:
            for item in items:
                updated = item.get("last_updated_at")
                if updated and updated > (iso or ""):
                    yield _item_to_card(item)
            if "LastEvaluatedKey" not in response:
                break
            response = self._table.query(
                IndexName=CARD_BY_KEY_INDEX,
                KeyConditionExpression="user_id = :uid",
                ExpressionAttributeValues={":uid": user_id},
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items = response.get("Items", [])

    def iter_all_cards(self, *, user_id: str = "default") -> Iterable[CardRecord]:
        response = self._table.query(
            IndexName=CARD_BY_KEY_INDEX,
            KeyConditionExpression="user_id = :uid",
            ExpressionAttributeValues={":uid": user_id},
        )
        items = response.get("Items", [])
        while True:
            for item in sorted(items, key=lambda row: str(row.get("simplified", ""))):
                yield _item_to_card(item)
            if "LastEvaluatedKey" not in response:
                break
            response = self._table.query(
                IndexName=CARD_BY_KEY_INDEX,
                KeyConditionExpression="user_id = :uid",
                ExpressionAttributeValues={":uid": user_id},
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items = response.get("Items", [])

    def get_drive_channel(self, channel_id: str) -> DriveChannelRecord | None:
        response = self._table.get_item(Key=_channel_keys(channel_id))
        item = response.get("Item")
        if not item:
            return None
        return _item_to_channel(item)

    def upsert_drive_channel(self, rec: DriveChannelRecord) -> None:
        item = {
            **_channel_keys(rec.channel_id),
            "entity_type": "channel",
            "channel_id": rec.channel_id,
            "resource_id": rec.resource_id,
            "page_token": rec.page_token,
            "expiration": dt_iso(rec.expiration),
            "schema_version": rec.schema_version,
            "user_id": rec.user_id,
            "source_set_name": rec.source_set_name,
            "channel_token": rec.channel_token,
            "last_advanced_at": dt_iso(rec.last_advanced_at),
        }
        self._table.put_item(Item=item)

    def list_drive_channels(self, *, user_id: str = "default") -> list[DriveChannelRecord]:
        response = self._table.scan(
            FilterExpression="entity_type = :etype AND user_id = :uid",
            ExpressionAttributeValues={":etype": "channel", ":uid": user_id},
        )
        return [_item_to_channel(item) for item in response.get("Items", [])]

    def advance_drive_channel_token(
        self,
        channel_id: str,
        *,
        expected_token: str,
        new_token: str,
    ) -> None:
        now = dt_iso(datetime.now(UTC))
        try:
            self._table.update_item(
                Key=_channel_keys(channel_id),
                UpdateExpression="SET page_token = :new_token, last_advanced_at = :now",
                ConditionExpression="page_token = :expected_token",
                ExpressionAttributeValues={
                    ":new_token": new_token,
                    ":expected_token": expected_token,
                    ":now": now,
                },
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                raise StateError("Conditional drive channel token advance failed") from exc
            raise StateError(str(exc)) from exc

    # ------------------------------------------------------------------ #
    # PendingEdits (M8)                                                    #
    # ------------------------------------------------------------------ #

    def upsert_pending_edit_debounced(
        self,
        *,
        user_id: str,
        source_set_name: str,
        file_id: str,
        now: datetime,
        quiet_seconds: int,
        max_delay_seconds: int,
    ) -> PendingEditRecord:
        from datetime import timedelta

        now_iso = dt_iso(now)
        ready_iso = dt_iso(now + timedelta(seconds=quiet_seconds))
        hard_iso = dt_iso(now + timedelta(seconds=max_delay_seconds))
        keys = _pending_edit_keys(user_id, source_set_name, file_id)
        # Fetch existing to preserve hard_deadline_at.
        existing_resp = self._table.get_item(Key=keys)
        existing = existing_resp.get("Item")

        if existing is None:
            item = {
                **keys,
                "entity_type": "pending_edit",
                "user_id": user_id,
                "source_set_name": source_set_name,
                "file_id": file_id,
                "first_seen_at": now_iso,
                "last_seen_at": now_iso,
                "ready_at": ready_iso,
                "hard_deadline_at": hard_iso,
                "force_process": False,
                "schema_version": 1,
            }
            self._table.put_item(Item=item)
        else:
            self._table.update_item(
                Key=keys,
                UpdateExpression="SET last_seen_at = :lsa, ready_at = :ra",
                ExpressionAttributeValues={":lsa": now_iso, ":ra": ready_iso},
            )
        resp2 = self._table.get_item(Key=keys)
        item2 = resp2.get("Item")
        assert item2 is not None
        return _item_to_pending_edit(item2)

    def list_ready_pending_edits(
        self,
        *,
        user_id: str,
        now: datetime,
    ) -> list[PendingEditRecord]:
        now_iso = dt_iso(now)
        response = self._table.scan(
            FilterExpression=(
                "entity_type = :etype AND user_id = :uid AND "
                "(force_process = :force OR ready_at <= :now OR hard_deadline_at <= :now)"
            ),
            ExpressionAttributeValues={
                ":etype": "pending_edit",
                ":uid": user_id,
                ":force": True,
                ":now": now_iso,
            },
        )
        return [_item_to_pending_edit(item) for item in response.get("Items", [])]

    def clear_pending_edit(
        self,
        *,
        user_id: str,
        source_set_name: str,
        file_id: str,
        if_last_seen_before: datetime,
    ) -> bool:
        guard_iso = dt_iso(if_last_seen_before)
        keys = _pending_edit_keys(user_id, source_set_name, file_id)
        try:
            self._table.delete_item(
                Key=keys,
                ConditionExpression="last_seen_at <= :guard",
                ExpressionAttributeValues={":guard": guard_iso},
            )
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                return False
            raise StateError(str(exc)) from exc

    def force_pending_edit(
        self,
        *,
        user_id: str,
        source_set_name: str,
        file_id: str,
    ) -> None:
        keys = _pending_edit_keys(user_id, source_set_name, file_id)
        self._table.update_item(
            Key=keys,
            UpdateExpression="SET force_process = :fp",
            ExpressionAttributeValues={":fp": True},
        )

    def get_pending_edit(
        self,
        *,
        user_id: str,
        source_set_name: str,
        file_id: str,
    ) -> PendingEditRecord | None:
        keys = _pending_edit_keys(user_id, source_set_name, file_id)
        response = self._table.get_item(Key=keys)
        item = response.get("Item")
        if not item:
            return None
        return _item_to_pending_edit(item)

    def record_run(self, rec: RunReportRecord) -> None:
        item = {
            **_run_keys(rec),
            "entity_type": "run",
            "run_id": rec.run_id,
            "trigger": rec.trigger,
            "started_at": dt_iso(rec.started_at),
            "finished_at": dt_iso(rec.finished_at),
            "sync_report_json": rec.sync_report_json,
            "schema_version": rec.schema_version,
            "user_id": rec.user_id,
        }
        self._table.put_item(Item=item)

    def iter_runs(self, *, limit: int = 100) -> Iterable[RunReportRecord]:
        response = self._table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeValues={":pk": "USER#default", ":prefix": "RUN#"},
            Limit=limit,
            ScanIndexForward=False,
        )
        for item in response.get("Items", []):
            yield _item_to_run(item)

    def get_run(self, run_id: str, *, user_id: str = "default") -> RunReportRecord | None:
        response = self._table.get_item(Key={"pk": f"USER#{user_id}", "sk": f"RUN#{run_id}"})
        item = response.get("Item")
        if not item:
            return None
        return _item_to_run(item)

    def update_run_report(self, run_id: str, sync_report_json: str, *, user_id: str = "default") -> None:
        self._table.update_item(
            Key={"pk": f"USER#{user_id}", "sk": f"RUN#{run_id}"},
            UpdateExpression="SET sync_report_json = :json",
            ExpressionAttributeValues={":json": sync_report_json},
        )

    def upsert_agent(self, rec: AgentRecord) -> None:
        item = {
            **_agent_keys(rec.user_id, rec.agent_id),
            "entity_type": "agent",
            "agent_id": rec.agent_id,
            "user_id": rec.user_id,
            "token_hash": rec.token_hash,
            "created_at": dt_iso(rec.created_at),
            "last_seen_at": dt_iso(rec.last_seen_at),
            "last_poll_at": dt_iso(rec.last_poll_at),
            "last_batch_id": rec.last_batch_id,
            "last_sync_status": rec.last_sync_status,
            "revoked_at": dt_iso(rec.revoked_at),
            "schema_version": rec.schema_version,
        }
        self._table.put_item(Item=item)

    def get_agent(self, agent_id: str, *, user_id: str = "default") -> AgentRecord | None:
        response = self._table.get_item(Key=_agent_keys(user_id, agent_id))
        item = response.get("Item")
        if not item:
            return None
        return _item_to_agent(item)

    def iter_agents(self, *, user_id: str = "default") -> Iterable[AgentRecord]:
        response = self._table.query(
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={":pk": f"agent#{user_id}"},
        )
        for item in response.get("Items", []):
            yield _item_to_agent(item)

    def revoke_agent(self, agent_id: str, *, user_id: str = "default") -> None:
        self._table.update_item(
            Key=_agent_keys(user_id, agent_id),
            UpdateExpression="SET revoked_at = :rev",
            ExpressionAttributeValues={":rev": dt_iso(datetime.now(UTC))},
        )

    def touch_agent_poll(
        self,
        agent_id: str,
        *,
        user_id: str = "default",
        batch_id: str = "",
        sync_status: str = "",
        seen_at: datetime | None = None,
    ) -> None:
        now = dt_iso(seen_at or datetime.now(UTC))
        values: dict[str, Any] = {":poll": now, ":seen": now, ":bid": batch_id}
        expr = "SET last_poll_at = :poll, last_seen_at = :seen, last_batch_id = :bid"
        if sync_status:
            expr += ", last_sync_status = :status"
            values[":status"] = sync_status
        self._table.update_item(
            Key=_agent_keys(user_id, agent_id),
            UpdateExpression=expr,
            ExpressionAttributeValues=values,
        )

    def get_agent_cursor(self, agent_id: str, *, user_id: str = "default") -> PendingSyncCursor | None:
        response = self._table.get_item(Key=_cursor_keys(user_id, agent_id))
        item = response.get("Item")
        if not item:
            return None
        return PendingSyncCursor(
            agent_id=str(item["agent_id"]),
            cursor_at=_parse_dt(item.get("cursor_at")),
            cursor_card_id=str(item.get("cursor_card_id", "")),
            schema_version=int(item.get("schema_version", 1)),
            user_id=str(item.get("user_id", user_id)),
        )

    def set_agent_cursor(self, rec: PendingSyncCursor) -> None:
        item = {
            **_cursor_keys(rec.user_id, rec.agent_id),
            "entity_type": "sync_cursor",
            "agent_id": rec.agent_id,
            "user_id": rec.user_id,
            "cursor_at": dt_iso(rec.cursor_at),
            "cursor_card_id": rec.cursor_card_id,
            "schema_version": rec.schema_version,
        }
        self._table.put_item(Item=item)

    def put_issued_batch(self, rec: IssuedBatchRecord) -> None:
        item: dict[str, Any] = {
            **_batch_keys(rec.batch_id),
            "entity_type": "issued_batch",
            "batch_id": rec.batch_id,
            "agent_id": rec.agent_id,
            "user_id": rec.user_id,
            "issued_at": dt_iso(rec.issued_at),
            "cursor_at": dt_iso(rec.cursor_at),
            "cursor_card_id": rec.cursor_card_id,
            "items_json": rec.items_json,
            "schema_version": rec.schema_version,
        }
        acked = dt_iso(rec.acked_at)
        if acked is not None:
            item["acked_at"] = acked
        self._table.put_item(Item=item)

    def get_issued_batch(self, batch_id: str) -> IssuedBatchRecord | None:
        response = self._table.get_item(Key=_batch_keys(batch_id))
        item = response.get("Item")
        if not item:
            return None
        return _item_to_batch(item)

    def get_open_batch_for_agent(self, agent_id: str, *, user_id: str = "default") -> IssuedBatchRecord | None:
        response = self._table.scan(
            FilterExpression="entity_type = :etype AND agent_id = :aid AND user_id = :uid AND attribute_not_exists(acked_at)",
            ExpressionAttributeValues={
                ":etype": "issued_batch",
                ":aid": agent_id,
                ":uid": user_id,
            },
        )
        items = response.get("Items", [])
        if not items:
            return None
        items.sort(key=lambda row: str(row.get("issued_at", "")), reverse=True)
        return _item_to_batch(items[0])

    def mark_batch_acked(self, batch_id: str, *, acked_at: datetime) -> None:
        try:
            self._table.update_item(
                Key=_batch_keys(batch_id),
                UpdateExpression="SET acked_at = :ack",
                ConditionExpression="attribute_not_exists(acked_at)",
                ExpressionAttributeValues={":ack": dt_iso(acked_at)},
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                raise StateError("Batch ack failed or already acknowledged") from exc
            raise StateError(str(exc)) from exc


def _item_to_agent(item: dict[str, Any]) -> AgentRecord:
    return AgentRecord(
        agent_id=str(item["agent_id"]),
        token_hash=str(item["token_hash"]),
        created_at=_parse_dt(item.get("created_at")),
        last_seen_at=_parse_dt(item.get("last_seen_at")),
        last_poll_at=_parse_dt(item.get("last_poll_at")),
        last_batch_id=str(item.get("last_batch_id", "")),
        last_sync_status=str(item.get("last_sync_status", "")),
        revoked_at=_parse_dt(item.get("revoked_at")),
        schema_version=int(item.get("schema_version", 1)),
        user_id=str(item.get("user_id", "default")),
    )


def _item_to_batch(item: dict[str, Any]) -> IssuedBatchRecord:
    return IssuedBatchRecord(
        batch_id=str(item["batch_id"]),
        agent_id=str(item["agent_id"]),
        user_id=str(item.get("user_id", "default")),
        issued_at=_parse_dt(item.get("issued_at")),
        acked_at=_parse_dt(item.get("acked_at")),
        cursor_at=_parse_dt(item.get("cursor_at")),
        cursor_card_id=str(item.get("cursor_card_id", "")),
        items_json=str(item.get("items_json", "[]")),
        schema_version=int(item.get("schema_version", 1)),
    )
