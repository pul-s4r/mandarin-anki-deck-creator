from __future__ import annotations

import re
import unicodedata


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


_DATE_LIKE = re.compile(
    r"^\s*(\d{1,2}[/\-]\d{1,2}|\d{4}-\d{2}-\d{2}|Last payment:|^\d+\s*\.?\s*$)",
    re.IGNORECASE,
)


def optional_drop_metadata_lines(text: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if _DATE_LIKE.match(stripped) and not any("\u4e00" <= ch <= "\u9fff" for ch in stripped):
            continue
        kept.append(line)
    return "\n".join(kept)
