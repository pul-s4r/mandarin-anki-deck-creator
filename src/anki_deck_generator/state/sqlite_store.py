"""SQLite-backed StateStore."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anki_deck_generator.errors import StateError
from anki_deck_generator.state.records import (
    CardRecord,
    CardUpsertResult,
    ChunkRecord,
    DriveChannelRecord,
    RunReportRecord,
    SourceRecord,
    compute_card_content_hash,
)

def _dt_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class SqliteStateStore:
    """One SQLite file per deployment; thread-safe writes via BEGIN IMMEDIATE."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(str(self._db_path), check_same_thread=False)
            c.row_factory = sqlite3.Row
            self._local.conn = c
            self._ensure_schema(c)
        return c

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'default',
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL,
                revision_id TEXT NOT NULL DEFAULT '',
                etag TEXT NOT NULL DEFAULT '',
                content_sha256 TEXT NOT NULL DEFAULT '',
                last_ingested_at TEXT,
                schema_version INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, provider, external_id)
            );
            CREATE TABLE IF NOT EXISTS chunks (
                source_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'default',
                chunk_sha256 TEXT NOT NULL,
                processed_at TEXT,
                model_id TEXT NOT NULL DEFAULT '',
                llm_output_card_ids TEXT NOT NULL DEFAULT '[]',
                schema_version INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (source_id, chunk_index)
            );
            CREATE TABLE IF NOT EXISTS cards (
                card_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'default',
                simplified TEXT NOT NULL,
                traditional TEXT NOT NULL DEFAULT '',
                pinyin TEXT NOT NULL DEFAULT '',
                meaning TEXT NOT NULL DEFAULT '',
                part_of_speech TEXT NOT NULL DEFAULT '',
                usage_notes TEXT NOT NULL DEFAULT '',
                sentence_simplified TEXT NOT NULL DEFAULT '',
                first_seen_source_id TEXT NOT NULL DEFAULT '',
                last_updated_at TEXT,
                content_hash TEXT NOT NULL DEFAULT '',
                schema_version INTEGER NOT NULL DEFAULT 1,
                ankiweb_note_id INTEGER,
                ankiweb_last_synced_at TEXT,
                ankiweb_last_synced_fields TEXT,
                UNIQUE(user_id, simplified)
            );
            CREATE TABLE IF NOT EXISTS drive_channels (
                channel_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'default',
                resource_id TEXT NOT NULL DEFAULT '',
                page_token TEXT NOT NULL DEFAULT '',
                expiration TEXT,
                schema_version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'default',
                trigger TEXT NOT NULL DEFAULT 'manual',
                started_at TEXT,
                finished_at TEXT,
                sync_report_json TEXT NOT NULL DEFAULT '{}',
                schema_version INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        conn.commit()

    def init_schema(self) -> None:
        """Create database file and schema (idempotent)."""
        self._ensure_schema(self._conn())

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    def _write(self, fn: Any) -> Any:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            out = fn(conn)
            conn.commit()
            return out
        except StateError:
            conn.rollback()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            raise StateError(str(exc)) from exc

    def get_source_record(self, provider: str, external_id: str, *, user_id: str = "default") -> SourceRecord | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM sources WHERE provider = ? AND external_id = ? AND user_id = ?",
            (provider, external_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_source(row)

    def upsert_source_record(self, rec: SourceRecord) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO sources (
                    source_id, user_id, provider, external_id, revision_id, etag,
                    content_sha256, last_ingested_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider, external_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    revision_id = excluded.revision_id,
                    etag = excluded.etag,
                    content_sha256 = excluded.content_sha256,
                    last_ingested_at = excluded.last_ingested_at,
                    schema_version = excluded.schema_version
                """,
                (
                    rec.source_id,
                    rec.user_id,
                    rec.provider,
                    rec.external_id,
                    rec.revision_id,
                    rec.etag,
                    rec.content_sha256,
                    _dt_iso(rec.last_ingested_at),
                    rec.schema_version,
                ),
            )

        self._write(op)

    def _row_to_source(self, row: sqlite3.Row) -> SourceRecord:
        return SourceRecord(
            source_id=row["source_id"],
            provider=row["provider"],
            external_id=row["external_id"],
            revision_id=row["revision_id"] or "",
            etag=row["etag"] or "",
            content_sha256=row["content_sha256"] or "",
            last_ingested_at=_parse_dt(row["last_ingested_at"]),
            schema_version=int(row["schema_version"]),
            user_id=row["user_id"] or "default",
        )

    def get_processed_chunk(self, source_id: str, chunk_index: int) -> ChunkRecord | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM chunks WHERE source_id = ? AND chunk_index = ?",
            (source_id, chunk_index),
        ).fetchone()
        if row is None:
            return None
        return ChunkRecord(
            source_id=row["source_id"],
            chunk_index=int(row["chunk_index"]),
            chunk_sha256=row["chunk_sha256"] or "",
            processed_at=_parse_dt(row["processed_at"]),
            model_id=row["model_id"] or "",
            llm_output_card_ids=json.loads(row["llm_output_card_ids"] or "[]"),
            schema_version=int(row["schema_version"]),
            user_id=row["user_id"] or "default",
        )

    def upsert_processed_chunk(self, rec: ChunkRecord) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO chunks (
                    source_id, chunk_index, user_id, chunk_sha256, processed_at,
                    model_id, llm_output_card_ids, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, chunk_index) DO UPDATE SET
                    chunk_sha256 = excluded.chunk_sha256,
                    processed_at = excluded.processed_at,
                    model_id = excluded.model_id,
                    llm_output_card_ids = excluded.llm_output_card_ids,
                    schema_version = excluded.schema_version
                """,
                (
                    rec.source_id,
                    rec.chunk_index,
                    rec.user_id,
                    rec.chunk_sha256,
                    _dt_iso(rec.processed_at),
                    rec.model_id,
                    json.dumps(rec.llm_output_card_ids),
                    rec.schema_version,
                ),
            )

        self._write(op)

    def get_card_by_key(self, natural_key: str, *, user_id: str = "default") -> CardRecord | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM cards WHERE simplified = ? AND user_id = ?",
            (natural_key, user_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_card(row)

    def get_card_by_id(self, card_id: str) -> CardRecord | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_card(row)

    def upsert_card(self, rec: CardRecord) -> CardUpsertResult:
        h = rec.content_hash or compute_card_content_hash(
            simplified=rec.simplified,
            traditional=rec.traditional,
            pinyin=rec.pinyin,
            meaning=rec.meaning,
            part_of_speech=rec.part_of_speech,
            usage_notes=rec.usage_notes,
        )

        def op(conn: sqlite3.Connection) -> CardUpsertResult:
            existing = conn.execute(
                "SELECT card_id, content_hash FROM cards WHERE simplified = ? AND user_id = ?",
                (rec.simplified, rec.user_id),
            ).fetchone()
            now_dt = rec.last_updated_at or datetime.now(UTC)
            now = _dt_iso(now_dt)
            if existing is None:
                cid = rec.card_id or str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO cards (
                        card_id, user_id, simplified, traditional, pinyin, meaning,
                        part_of_speech, usage_notes, sentence_simplified,
                        first_seen_source_id, last_updated_at, content_hash, schema_version,
                        ankiweb_note_id, ankiweb_last_synced_at, ankiweb_last_synced_fields
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cid,
                        rec.user_id,
                        rec.simplified,
                        rec.traditional,
                        rec.pinyin,
                        rec.meaning,
                        rec.part_of_speech,
                        rec.usage_notes,
                        rec.sentence_simplified,
                        rec.first_seen_source_id,
                        now,
                        h,
                        rec.schema_version,
                        rec.ankiweb_note_id,
                        _dt_iso(rec.ankiweb_last_synced_at),
                        json.dumps(rec.ankiweb_last_synced_fields) if rec.ankiweb_last_synced_fields else None,
                    ),
                )
                return CardUpsertResult.CREATED
            if (existing["content_hash"] or "") == h:
                return CardUpsertResult.UNCHANGED
            now_up = _dt_iso(rec.last_updated_at or datetime.now(UTC))
            conn.execute(
                """
                UPDATE cards SET
                    traditional = ?, pinyin = ?, meaning = ?, part_of_speech = ?,
                    usage_notes = ?, sentence_simplified = ?, first_seen_source_id = ?,
                    last_updated_at = ?, content_hash = ?, schema_version = ?,
                    ankiweb_note_id = ?, ankiweb_last_synced_at = ?, ankiweb_last_synced_fields = ?
                WHERE card_id = ?
                """,
                (
                    rec.traditional,
                    rec.pinyin,
                    rec.meaning,
                    rec.part_of_speech,
                    rec.usage_notes,
                    rec.sentence_simplified,
                    rec.first_seen_source_id,
                    now_up,
                    h,
                    rec.schema_version,
                    rec.ankiweb_note_id,
                    _dt_iso(rec.ankiweb_last_synced_at),
                    json.dumps(rec.ankiweb_last_synced_fields) if rec.ankiweb_last_synced_fields else None,
                    existing["card_id"],
                ),
            )
            return CardUpsertResult.UPDATED

        return self._write(op)

    def _row_to_card(self, row: sqlite3.Row) -> CardRecord:
        raw_fields = row["ankiweb_last_synced_fields"]
        fields: dict[str, str] | None
        if raw_fields:
            try:
                loaded = json.loads(raw_fields)
                fields = {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                fields = None
        else:
            fields = None
        return CardRecord(
            card_id=row["card_id"],
            simplified=row["simplified"],
            traditional=row["traditional"] or "",
            pinyin=row["pinyin"] or "",
            meaning=row["meaning"] or "",
            part_of_speech=row["part_of_speech"] or "",
            usage_notes=row["usage_notes"] or "",
            sentence_simplified=row["sentence_simplified"] or "",
            first_seen_source_id=row["first_seen_source_id"] or "",
            last_updated_at=_parse_dt(row["last_updated_at"]),
            content_hash=row["content_hash"] or "",
            schema_version=int(row["schema_version"]),
            user_id=row["user_id"] or "default",
            ankiweb_note_id=row["ankiweb_note_id"],
            ankiweb_last_synced_at=_parse_dt(row["ankiweb_last_synced_at"]),
            ankiweb_last_synced_fields=fields,
        )

    def iter_cards_changed_since(self, ts: datetime, *, user_id: str = "default") -> Iterable[CardRecord]:
        conn = self._conn()
        iso = _dt_iso(ts)
        for row in conn.execute(
            "SELECT * FROM cards WHERE user_id = ? AND last_updated_at > ? ORDER BY last_updated_at",
            (user_id, iso),
        ):
            yield self._row_to_card(row)

    def iter_all_cards(self, *, user_id: str = "default") -> Iterable[CardRecord]:
        conn = self._conn()
        for row in conn.execute(
            "SELECT * FROM cards WHERE user_id = ? ORDER BY simplified",
            (user_id,),
        ):
            yield self._row_to_card(row)

    def get_drive_channel(self, channel_id: str) -> DriveChannelRecord | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM drive_channels WHERE channel_id = ?", (channel_id,)).fetchone()
        if row is None:
            return None
        return DriveChannelRecord(
            channel_id=row["channel_id"],
            resource_id=row["resource_id"] or "",
            page_token=row["page_token"] or "",
            expiration=_parse_dt(row["expiration"]),
            schema_version=int(row["schema_version"]),
            user_id=row["user_id"] or "default",
        )

    def upsert_drive_channel(self, rec: DriveChannelRecord) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO drive_channels (
                    channel_id, user_id, resource_id, page_token, expiration, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    resource_id = excluded.resource_id,
                    page_token = excluded.page_token,
                    expiration = excluded.expiration,
                    schema_version = excluded.schema_version
                """,
                (
                    rec.channel_id,
                    rec.user_id,
                    rec.resource_id,
                    rec.page_token,
                    _dt_iso(rec.expiration),
                    rec.schema_version,
                ),
            )

        self._write(op)

    def record_run(self, rec: RunReportRecord) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, user_id, trigger, started_at, finished_at, sync_report_json, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.run_id,
                    rec.user_id,
                    rec.trigger,
                    _dt_iso(rec.started_at),
                    _dt_iso(rec.finished_at),
                    rec.sync_report_json,
                    rec.schema_version,
                ),
            )

        self._write(op)

    def iter_runs(self, *, limit: int = 100) -> Iterable[RunReportRecord]:
        conn = self._conn()
        for row in conn.execute(
            "SELECT * FROM runs ORDER BY COALESCE(started_at, finished_at, run_id) DESC LIMIT ?",
            (limit,),
        ):
            yield RunReportRecord(
                run_id=row["run_id"],
                trigger=row["trigger"] or "manual",
                started_at=_parse_dt(row["started_at"]),
                finished_at=_parse_dt(row["finished_at"]),
                sync_report_json=row["sync_report_json"] or "{}",
                schema_version=int(row["schema_version"]),
                user_id=row["user_id"] or "default",
            )
