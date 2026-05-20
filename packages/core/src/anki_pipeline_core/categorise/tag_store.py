from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from anki_pipeline_core.models import Tag


class TagStore(Protocol):
    """Tag CRUD for shared categorisation data."""

    def get_tag(self, tag_id: str, *, user_id: str = "default") -> Tag | None: ...

    def list_tags_for_term(self, term_id: str, *, user_id: str = "default") -> list[Tag]: ...

    def upsert_tag_if_not_confirmed(self, tag: Tag) -> bool:
        """Insert or update when ``confirmed`` is false. Returns True if written."""

    def confirm_tag(self, tag_id: str, *, user_id: str = "default") -> None: ...
