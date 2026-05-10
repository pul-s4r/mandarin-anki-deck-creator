from __future__ import annotations

from pathlib import Path

from anki_deck_generator.config.settings import Settings
from anki_deck_generator.state import get_store
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
