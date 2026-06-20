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
from anki_deck_generator.state.card_compare import ankiweb_meta_matches_stored, dt_iso, normalize_stored_anki_fields
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

def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _ensure_drive_channel_m8_columns(conn: sqlite3.Connection) -> None:
    """Add M8 columns to drive_channels when upgrading pre-M8 databases (idempotent)."""
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='drive_channels' LIMIT 1"
        ).fetchone()
        is None
    ):
        return
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(drive_channels)")}
    if "source_set_name" not in cols:
        conn.execute("ALTER TABLE drive_channels ADD COLUMN source_set_name TEXT NOT NULL DEFAULT ''")
    if "channel_token" not in cols:
        conn.execute("ALTER TABLE drive_channels ADD COLUMN channel_token TEXT NOT NULL DEFAULT ''")
    if "last_advanced_at" not in cols:
        conn.execute("ALTER TABLE drive_channels ADD COLUMN last_advanced_at TEXT")


def _ensure_cards_ankiweb_columns(conn: sqlite3.Connection) -> None:
    """Add ankiweb_* columns when upgrading pre-M4 databases (idempotent)."""
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cards' LIMIT 1"
        ).fetchone()
        is None
    ):
        return
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(cards)")}
    if "ankiweb_note_id" not in cols:
        conn.execute("ALTER TABLE cards ADD COLUMN ankiweb_note_id INTEGER")
    if "ankiweb_last_synced_at" not in cols:
        conn.execute("ALTER TABLE cards ADD COLUMN ankiweb_last_synced_at TEXT")
    if "ankiweb_last_synced_fields" not in cols:
        conn.execute("ALTER TABLE cards ADD COLUMN ankiweb_last_synced_fields TEXT")


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
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'default',
                token_hash TEXT NOT NULL,
                created_at TEXT,
                last_seen_at TEXT,
                last_poll_at TEXT,
                last_batch_id TEXT NOT NULL DEFAULT '',
                last_sync_status TEXT NOT NULL DEFAULT '',
                revoked_at TEXT,
                schema_version INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (user_id, agent_id)
            );
            CREATE TABLE IF NOT EXISTS agent_cursors (
                agent_id TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'default',
                cursor_at TEXT,
                cursor_card_id TEXT NOT NULL DEFAULT '',
                schema_version INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (user_id, agent_id)
            );
            CREATE TABLE IF NOT EXISTS issued_batches (
                batch_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'default',
                issued_at TEXT,
                acked_at TEXT,
                cursor_at TEXT,
                cursor_card_id TEXT NOT NULL DEFAULT '',
                items_json TEXT NOT NULL DEFAULT '[]',
                schema_version INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_issued_batches_open
                ON issued_batches(user_id, agent_id, acked_at);
            CREATE TABLE IF NOT EXISTS pending_edits (
                user_id TEXT NOT NULL DEFAULT 'default',
                source_set_name TEXT NOT NULL,
                file_id TEXT NOT NULL,
                first_seen_at TEXT,
                last_seen_at TEXT,
                ready_at TEXT,
                hard_deadline_at TEXT,
                force_process INTEGER NOT NULL DEFAULT 0,
                schema_version INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (user_id, source_set_name, file_id)
            );
            CREATE INDEX IF NOT EXISTS idx_pending_edits_ready
                ON pending_edits(user_id, ready_at);
            """
        )
        _ensure_cards_ankiweb_columns(conn)
        _ensure_drive_channel_m8_columns(conn)
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
                    dt_iso(rec.last_ingested_at),
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
                    dt_iso(rec.processed_at),
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
            now = dt_iso(now_dt)
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
                        dt_iso(rec.ankiweb_last_synced_at),
                        json.dumps(rec.ankiweb_last_synced_fields) if rec.ankiweb_last_synced_fields else None,
                    ),
                )
                return CardUpsertResult.CREATED
            if (existing["content_hash"] or "") == h:
                detail = conn.execute(
                    "SELECT ankiweb_note_id, ankiweb_last_synced_at, ankiweb_last_synced_fields "
                    "FROM cards WHERE card_id = ?",
                    (existing["card_id"],),
                ).fetchone()
                if detail is not None and ankiweb_meta_matches_stored(
                    stored_note_id=detail["ankiweb_note_id"],
                    stored_synced_at=_parse_dt(detail["ankiweb_last_synced_at"]),
                    stored_synced_fields=detail["ankiweb_last_synced_fields"],
                    rec=rec,
                ):
                    return CardUpsertResult.UNCHANGED
            now_up = dt_iso(rec.last_updated_at or datetime.now(UTC))
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
                    dt_iso(rec.ankiweb_last_synced_at),
                    json.dumps(rec.ankiweb_last_synced_fields) if rec.ankiweb_last_synced_fields else None,
                    existing["card_id"],
                ),
            )
            return CardUpsertResult.UPDATED

        return self._write(op)

    def _row_to_card(self, row: sqlite3.Row) -> CardRecord:
        raw_fields = row["ankiweb_last_synced_fields"]
        normalized = normalize_stored_anki_fields(raw_fields)
        fields: dict[str, str] | None = normalized or None
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
        iso = dt_iso(ts)
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

    def _row_to_drive_channel(self, row: sqlite3.Row) -> DriveChannelRecord:
        keys = {r[1] for r in self._conn().execute("PRAGMA table_info(drive_channels)")}
        return DriveChannelRecord(
            channel_id=row["channel_id"],
            resource_id=row["resource_id"] or "",
            page_token=row["page_token"] or "",
            expiration=_parse_dt(row["expiration"]),
            schema_version=int(row["schema_version"]),
            user_id=row["user_id"] or "default",
            source_set_name=row["source_set_name"] if "source_set_name" in keys else "",
            channel_token=row["channel_token"] if "channel_token" in keys else "",
            last_advanced_at=_parse_dt(row["last_advanced_at"]) if "last_advanced_at" in keys else None,
        )

    def get_drive_channel(self, channel_id: str) -> DriveChannelRecord | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM drive_channels WHERE channel_id = ?", (channel_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_drive_channel(row)

    def list_drive_channels(self, *, user_id: str = "default") -> list[DriveChannelRecord]:
        """Return all drive channels for the given user."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM drive_channels WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [self._row_to_drive_channel(r) for r in rows]

    def upsert_drive_channel(self, rec: DriveChannelRecord) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO drive_channels (
                    channel_id, user_id, resource_id, page_token, expiration, schema_version,
                    source_set_name, channel_token, last_advanced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    resource_id = excluded.resource_id,
                    page_token = excluded.page_token,
                    expiration = excluded.expiration,
                    schema_version = excluded.schema_version,
                    source_set_name = excluded.source_set_name,
                    channel_token = excluded.channel_token,
                    last_advanced_at = excluded.last_advanced_at
                """,
                (
                    rec.channel_id,
                    rec.user_id,
                    rec.resource_id,
                    rec.page_token,
                    dt_iso(rec.expiration),
                    rec.schema_version,
                    rec.source_set_name,
                    rec.channel_token,
                    dt_iso(rec.last_advanced_at),
                ),
            )

        self._write(op)

    def advance_drive_channel_token(
        self,
        channel_id: str,
        *,
        expected_token: str,
        new_token: str,
    ) -> None:
        now = dt_iso(datetime.now(UTC))

        def op(conn: sqlite3.Connection) -> None:
            cur = conn.execute(
                """
                UPDATE drive_channels
                SET page_token = ?, last_advanced_at = ?
                WHERE channel_id = ? AND page_token = ?
                """,
                (new_token, now, channel_id, expected_token),
            )
            if cur.rowcount != 1:
                raise StateError("Conditional drive channel token advance failed")

        self._write(op)

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

        def op(conn: sqlite3.Connection) -> PendingEditRecord | None:
            existing = conn.execute(
                "SELECT * FROM pending_edits WHERE user_id = ? AND source_set_name = ? AND file_id = ?",
                (user_id, source_set_name, file_id),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO pending_edits
                        (user_id, source_set_name, file_id, first_seen_at, last_seen_at,
                         ready_at, hard_deadline_at, force_process, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)
                    """,
                    (user_id, source_set_name, file_id, now_iso, now_iso, ready_iso, hard_iso),
                )
                return None
            # Update last_seen_at and push ready_at forward (slide quiet window).
            # Preserve hard_deadline_at from first_seen to enforce max delay.
            conn.execute(
                """
                UPDATE pending_edits
                SET last_seen_at = ?, ready_at = ?
                WHERE user_id = ? AND source_set_name = ? AND file_id = ?
                """,
                (now_iso, ready_iso, user_id, source_set_name, file_id),
            )
            return None

        self._write(op)
        row = self._conn().execute(
            "SELECT * FROM pending_edits WHERE user_id = ? AND source_set_name = ? AND file_id = ?",
            (user_id, source_set_name, file_id),
        ).fetchone()
        assert row is not None
        return self._row_to_pending_edit(row)

    def list_ready_pending_edits(
        self,
        *,
        user_id: str,
        now: datetime,
    ) -> list[PendingEditRecord]:
        now_iso = dt_iso(now)
        conn = self._conn()
        rows = conn.execute(
            """
            SELECT * FROM pending_edits
            WHERE user_id = ?
              AND (force_process = 1 OR ready_at <= ? OR hard_deadline_at <= ?)
            ORDER BY hard_deadline_at ASC, ready_at ASC
            """,
            (user_id, now_iso, now_iso),
        ).fetchall()
        return [self._row_to_pending_edit(r) for r in rows]

    def clear_pending_edit(
        self,
        *,
        user_id: str,
        source_set_name: str,
        file_id: str,
        if_last_seen_before: datetime,
    ) -> bool:
        """Delete the pending edit iff last_seen_at <= if_last_seen_before.

        Returns True if a row was deleted (guard held).
        """
        guard_iso = dt_iso(if_last_seen_before)

        def op(conn: sqlite3.Connection) -> bool:
            cur = conn.execute(
                """
                DELETE FROM pending_edits
                WHERE user_id = ? AND source_set_name = ? AND file_id = ?
                  AND last_seen_at <= ?
                """,
                (user_id, source_set_name, file_id, guard_iso),
            )
            return cur.rowcount == 1

        return self._write(op)

    def force_pending_edit(
        self,
        *,
        user_id: str,
        source_set_name: str,
        file_id: str,
    ) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                UPDATE pending_edits SET force_process = 1
                WHERE user_id = ? AND source_set_name = ? AND file_id = ?
                """,
                (user_id, source_set_name, file_id),
            )

        self._write(op)

    def get_pending_edit(
        self,
        *,
        user_id: str,
        source_set_name: str,
        file_id: str,
    ) -> PendingEditRecord | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM pending_edits WHERE user_id = ? AND source_set_name = ? AND file_id = ?",
            (user_id, source_set_name, file_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_pending_edit(row)

    def _row_to_pending_edit(self, row: sqlite3.Row) -> PendingEditRecord:
        return PendingEditRecord(
            user_id=row["user_id"] or "default",
            source_set_name=row["source_set_name"] or "",
            file_id=row["file_id"] or "",
            first_seen_at=_parse_dt(row["first_seen_at"]),
            last_seen_at=_parse_dt(row["last_seen_at"]),
            ready_at=_parse_dt(row["ready_at"]),
            hard_deadline_at=_parse_dt(row["hard_deadline_at"]),
            force_process=bool(row["force_process"]),
            schema_version=int(row["schema_version"]),
        )

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
                    dt_iso(rec.started_at),
                    dt_iso(rec.finished_at),
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

    def get_run(self, run_id: str, *, user_id: str = "default") -> RunReportRecord | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ? AND user_id = ?",
            (run_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return RunReportRecord(
            run_id=row["run_id"],
            trigger=row["trigger"] or "manual",
            started_at=_parse_dt(row["started_at"]),
            finished_at=_parse_dt(row["finished_at"]),
            sync_report_json=row["sync_report_json"] or "{}",
            schema_version=int(row["schema_version"]),
            user_id=row["user_id"] or "default",
        )

    def update_run_report(self, run_id: str, sync_report_json: str, *, user_id: str = "default") -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE runs SET sync_report_json = ? WHERE run_id = ? AND user_id = ?",
                (sync_report_json, run_id, user_id),
            )

        self._write(op)

    def upsert_agent(self, rec: AgentRecord) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO agents (
                    agent_id, user_id, token_hash, created_at, last_seen_at, last_poll_at,
                    last_batch_id, last_sync_status, revoked_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, agent_id) DO UPDATE SET
                    token_hash = excluded.token_hash,
                    last_seen_at = excluded.last_seen_at,
                    last_poll_at = excluded.last_poll_at,
                    last_batch_id = excluded.last_batch_id,
                    last_sync_status = excluded.last_sync_status,
                    revoked_at = excluded.revoked_at,
                    schema_version = excluded.schema_version
                """,
                (
                    rec.agent_id,
                    rec.user_id,
                    rec.token_hash,
                    dt_iso(rec.created_at),
                    dt_iso(rec.last_seen_at),
                    dt_iso(rec.last_poll_at),
                    rec.last_batch_id,
                    rec.last_sync_status,
                    dt_iso(rec.revoked_at),
                    rec.schema_version,
                ),
            )

        self._write(op)

    def get_agent(self, agent_id: str, *, user_id: str = "default") -> AgentRecord | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM agents WHERE agent_id = ? AND user_id = ?",
            (agent_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_agent(row)

    def iter_agents(self, *, user_id: str = "default") -> Iterable[AgentRecord]:
        conn = self._conn()
        for row in conn.execute("SELECT * FROM agents WHERE user_id = ?", (user_id,)):
            yield self._row_to_agent(row)

    def _row_to_agent(self, row: sqlite3.Row) -> AgentRecord:
        return AgentRecord(
            agent_id=row["agent_id"],
            token_hash=row["token_hash"],
            created_at=_parse_dt(row["created_at"]),
            last_seen_at=_parse_dt(row["last_seen_at"]),
            last_poll_at=_parse_dt(row["last_poll_at"]),
            last_batch_id=row["last_batch_id"] or "",
            last_sync_status=row["last_sync_status"] or "",
            revoked_at=_parse_dt(row["revoked_at"]),
            schema_version=int(row["schema_version"]),
            user_id=row["user_id"] or "default",
        )

    def revoke_agent(self, agent_id: str, *, user_id: str = "default") -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE agents SET revoked_at = ? WHERE agent_id = ? AND user_id = ?",
                (dt_iso(datetime.now(UTC)), agent_id, user_id),
            )

        self._write(op)

    def touch_agent_poll(
        self,
        agent_id: str,
        *,
        user_id: str = "default",
        batch_id: str = "",
        sync_status: str = "",
        seen_at: datetime | None = None,
    ) -> None:
        now = seen_at or datetime.now(UTC)

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                UPDATE agents SET last_poll_at = ?, last_seen_at = ?, last_batch_id = ?,
                    last_sync_status = CASE WHEN ? != '' THEN ? ELSE last_sync_status END
                WHERE agent_id = ? AND user_id = ?
                """,
                (dt_iso(now), dt_iso(now), batch_id, sync_status, sync_status, agent_id, user_id),
            )

        self._write(op)

    def get_agent_cursor(self, agent_id: str, *, user_id: str = "default") -> PendingSyncCursor | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM agent_cursors WHERE agent_id = ? AND user_id = ?",
            (agent_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return PendingSyncCursor(
            agent_id=row["agent_id"],
            cursor_at=_parse_dt(row["cursor_at"]),
            cursor_card_id=row["cursor_card_id"] or "",
            schema_version=int(row["schema_version"]),
            user_id=row["user_id"] or "default",
        )

    def set_agent_cursor(self, rec: PendingSyncCursor) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO agent_cursors (agent_id, user_id, cursor_at, cursor_card_id, schema_version)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, agent_id) DO UPDATE SET
                    cursor_at = excluded.cursor_at,
                    cursor_card_id = excluded.cursor_card_id,
                    schema_version = excluded.schema_version
                """,
                (
                    rec.agent_id,
                    rec.user_id,
                    dt_iso(rec.cursor_at),
                    rec.cursor_card_id,
                    rec.schema_version,
                ),
            )

        self._write(op)

    def put_issued_batch(self, rec: IssuedBatchRecord) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO issued_batches (
                    batch_id, agent_id, user_id, issued_at, acked_at,
                    cursor_at, cursor_card_id, items_json, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.batch_id,
                    rec.agent_id,
                    rec.user_id,
                    dt_iso(rec.issued_at),
                    dt_iso(rec.acked_at),
                    dt_iso(rec.cursor_at),
                    rec.cursor_card_id,
                    rec.items_json,
                    rec.schema_version,
                ),
            )

        self._write(op)

    def get_issued_batch(self, batch_id: str) -> IssuedBatchRecord | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM issued_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_batch(row)

    def get_open_batch_for_agent(self, agent_id: str, *, user_id: str = "default") -> IssuedBatchRecord | None:
        conn = self._conn()
        row = conn.execute(
            """
            SELECT * FROM issued_batches
            WHERE agent_id = ? AND user_id = ? AND acked_at IS NULL
            ORDER BY issued_at DESC LIMIT 1
            """,
            (agent_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_batch(row)

    def _row_to_batch(self, row: sqlite3.Row) -> IssuedBatchRecord:
        return IssuedBatchRecord(
            batch_id=row["batch_id"],
            agent_id=row["agent_id"],
            user_id=row["user_id"] or "default",
            issued_at=_parse_dt(row["issued_at"]),
            acked_at=_parse_dt(row["acked_at"]),
            cursor_at=_parse_dt(row["cursor_at"]),
            cursor_card_id=row["cursor_card_id"] or "",
            items_json=row["items_json"] or "[]",
            schema_version=int(row["schema_version"]),
        )

    def mark_batch_acked(self, batch_id: str, *, acked_at: datetime) -> None:
        def op(conn: sqlite3.Connection) -> None:
            cur = conn.execute(
                "UPDATE issued_batches SET acked_at = ? WHERE batch_id = ? AND acked_at IS NULL",
                (dt_iso(acked_at), batch_id),
            )
            if cur.rowcount != 1:
                raise StateError("Batch ack failed or already acknowledged")

        self._write(op)
