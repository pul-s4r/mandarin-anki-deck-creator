from datetime import UTC, datetime

from anki_pipeline_core.models import Tag
from anki_pipeline_core.state import SqliteTagStore


def test_tag_store_confirm_and_write_guard(tmp_path) -> None:
    db = tmp_path / "state.db"
    store = SqliteTagStore(db)
    store.init_schema()
    tag = Tag(
        tag_id="tag-1",
        term_id="term-1",
        dimension="topic",
        value="Housing",
        source="inferred",
        confirmed=False,
        user_id="default",
    )
    assert store.upsert_tag_if_not_confirmed(tag) is True
    tag.confirmed = True
    tag.source = "user"
    store.confirm_tag("tag-1")
    updated = Tag(
        tag_id="tag-2",
        term_id="term-1",
        dimension="topic",
        value="Housing",
        source="inferred",
        confirmed=False,
        updated_at=datetime.now(UTC),
        user_id="default",
    )
    assert store.upsert_tag_if_not_confirmed(updated) is False
    loaded = store.get_tag("tag-1")
    assert loaded is not None
    assert loaded.confirmed is True
    assert loaded.value == "Housing"
