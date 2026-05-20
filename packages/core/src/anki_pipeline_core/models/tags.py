from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

TagDimension = Literal["lesson_date", "topic", "custom"]
TagSource = Literal["explicit", "inferred", "user"]


@dataclass
class Tag:
    tag_id: str
    term_id: str
    dimension: TagDimension
    value: str
    source: TagSource = "inferred"
    confirmed: bool = False
    created_at: datetime | None = None
    created_by: str = "generator"
    updated_at: datetime | None = None
    user_id: str = "default"
