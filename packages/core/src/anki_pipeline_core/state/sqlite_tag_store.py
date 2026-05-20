"""SQLite-backed TagStore (shared tags table)."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from anki_pipeline_core.models import Tag


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


class SqliteTagStore:
    """Tags table in the pipeline state database (WAL, thread-safe writes)."""

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
            CREATE TABLE IF NOT EXISTS tags (
                tag_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'default',
                term_id TEXT NOT NULL,
                dimension TEXT NOT NULL,
                value TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'inferred',
                confirmed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT,
                created_by TEXT NOT NULL DEFAULT 'generator',
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tags_user_term
                ON tags (user_id, term_id);
            """
        )
        conn.commit()

    def init_schema(self) -> None:
        self._ensure_schema(self._conn())

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    def _row_to_tag(self, row: sqlite3.Row) -> Tag:
        return Tag(
            tag_id=row["tag_id"],
            term_id=row["term_id"],
            dimension=row["dimension"],  # type: ignore[arg-type]
            value=row["value"],
            source=row["source"],  # type: ignore[arg-type]
            confirmed=bool(row["confirmed"]),
            created_at=_parse_dt(row["created_at"]),
            created_by=row["created_by"] or "generator",
            updated_at=_parse_dt(row["updated_at"]),
            user_id=row["user_id"] or "default",
        )

    def get_tag(self, tag_id: str, *, user_id: str = "default") -> Tag | None:
        row = self._conn().execute(
            "SELECT * FROM tags WHERE tag_id = ? AND user_id = ?",
            (tag_id, user_id),
        ).fetchone()
        return self._row_to_tag(row) if row else None

    def list_tags_for_term(self, term_id: str, *, user_id: str = "default") -> list[Tag]:
        rows = self._conn().execute(
            "SELECT * FROM tags WHERE term_id = ? AND user_id = ? ORDER BY dimension, value",
            (term_id, user_id),
        )
        return [self._row_to_tag(r) for r in rows]

    def upsert_tag_if_not_confirmed(self, tag: Tag) -> bool:
        conn = self._conn()
        existing = conn.execute(
            """
            SELECT tag_id, confirmed FROM tags
            WHERE user_id = ? AND term_id = ? AND dimension = ? AND value = ?
            """,
            (tag.user_id, tag.term_id, tag.dimension, tag.value),
        ).fetchone()
        now = _dt_iso(tag.updated_at or datetime.now(UTC))
        created = _dt_iso(tag.created_at or datetime.now(UTC))
        if existing is not None and bool(existing["confirmed"]):
            return False
        tag_id = tag.tag_id or (existing["tag_id"] if existing else str(uuid.uuid4()))
        conn.execute("BEGIN IMMEDIATE")
        try:
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO tags (
                        tag_id, user_id, term_id, dimension, value, source, confirmed,
                        created_at, created_by, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tag_id,
                        tag.user_id,
                        tag.term_id,
                        tag.dimension,
                        tag.value,
                        tag.source,
                        1 if tag.confirmed else 0,
                        created,
                        tag.created_by,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE tags SET source = ?, confirmed = ?, updated_at = ?, created_by = ?
                    WHERE tag_id = ? AND confirmed = 0
                    """,
                    (tag.source, 1 if tag.confirmed else 0, now, tag.created_by, tag_id),
                )
                if conn.total_changes == 0:
                    conn.rollback()
                    return False
            conn.commit()
            return True
        except sqlite3.Error:
            conn.rollback()
            raise

    def confirm_tag(self, tag_id: str, *, user_id: str = "default") -> None:
        conn = self._conn()
        now = _dt_iso(datetime.now(UTC))
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                UPDATE tags SET confirmed = 1, source = 'user', updated_at = ?
                WHERE tag_id = ? AND user_id = ?
                """,
                (now, tag_id, user_id),
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
