from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CedictEntry:
    traditional: str
    simplified: str
    pinyin_raw: str
    glosses: tuple[str, ...]


class CedictParser:
    def parse_line(self, line: str) -> Optional[CedictEntry]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None
        parts = stripped.split()
        if len(parts) < 3:
            logger.debug("cedict skip (too few tokens): %s", stripped[:80])
            return None
        traditional, simplified = parts[0], parts[1]
        rest = stripped[len(traditional) + 1 + len(simplified) :].lstrip()
        if not rest.startswith("["):
            logger.debug("cedict skip (no pinyin bracket): %s", stripped[:80])
            return None
        close = rest.find("]")
        if close == -1:
            logger.debug("cedict skip (unclosed pinyin): %s", stripped[:80])
            return None
        pinyin_raw = rest[1:close].strip()
        tail = rest[close + 1 :].lstrip()
        if not tail.startswith("/"):
            logger.debug("cedict skip (no gloss slash): %s", stripped[:80])
            return None
        last_slash = tail.rfind("/")
        if last_slash <= 0:
            logger.debug("cedict skip (no gloss close): %s", stripped[:80])
            return None
        gloss_run = tail[: last_slash + 1]
        trailing = tail[last_slash + 1 :].strip()
        if trailing:
            logger.debug("cedict skip (trailing after gloss): %s", stripped[:80])
            return None
        inner = gloss_run.strip("/")
        glosses = tuple(g for g in inner.split("/") if g)
        if not glosses:
            logger.debug("cedict skip (empty glosses): %s", stripped[:80])
            return None
        return CedictEntry(
            traditional=traditional,
            simplified=simplified,
            pinyin_raw=pinyin_raw,
            glosses=glosses,
        )
