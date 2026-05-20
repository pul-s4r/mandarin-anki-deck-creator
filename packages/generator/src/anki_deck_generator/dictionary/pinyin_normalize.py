from __future__ import annotations

import re

_SYLLABLE = re.compile(r"^([a-zA-ZüÜ]+)(\d)$")


def cedict_pinyin_to_tone_marks(pinyin_raw: str) -> str:
    parts = pinyin_raw.split()
    return " ".join(_syllable_to_marked(p) for p in parts if p)


def _syllable_to_marked(token: str) -> str:
    token = token.strip()
    if not token:
        return token
    m = _SYLLABLE.match(token)
    if not m:
        return token.lower()
    body, tone_s = m.group(1), int(m.group(2))
    if tone_s == 5 or tone_s == 0:
        return body.lower()
    if tone_s < 1 or tone_s > 4:
        return body.lower()
    return _apply_tone_mark(body.lower(), tone_s)


def _apply_tone_mark(syllable: str, tone: int) -> str:
    s = syllable.replace("ü", "v")  # work with v internally, restore ü
    idx = _vowel_index_for_tone(s)
    if idx < 0:
        return syllable
    ch = s[idx]
    mapped = _map_vowel(ch, tone)
    if mapped is None:
        return syllable
    out = list(s)
    out[idx] = mapped
    result = "".join(out).replace("v", "ü")
    return result


def _vowel_index_for_tone(s: str) -> int:
    if "a" in s:
        return s.index("a")
    if "e" in s:
        return s.index("e")
    if "ou" in s:
        return s.index("o")
    if "uo" in s:
        return s.index("o")
    if "iu" in s:
        return s.rindex("u")
    if "ui" in s:
        return s.index("i")
    vowels = "aeiouv"
    positions = [i for i, c in enumerate(s) if c in vowels]
    if not positions:
        return -1
    return positions[-1]


def _map_vowel(ch: str, tone: int) -> str | None:
    tone -= 1
    table = {
        "a": "āáǎà",
        "e": "ēéěè",
        "i": "īíǐì",
        "o": "ōóǒò",
        "u": "ūúǔù",
        "v": "ǖǘǚǜ",
    }
    row = table.get(ch)
    if not row:
        return None
    return row[tone]
