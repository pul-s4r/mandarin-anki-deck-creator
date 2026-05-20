from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedSentence:
    text: str
    source: str


_RE_DIALOGUE_HEADER = re.compile(r"^\s*dialogues?\s*[:：]\s*$", re.IGNORECASE)
_RE_SPEAKER_LINE = re.compile(r"^\s*([A-Za-z])\s*[:：]\s*(.+?)\s*$")
_RE_SECTION_HEADERISH = re.compile(r"^\s*[\w\s-]{2,40}[:：]\s*$")

# PDF text extraction sometimes introduces invisible format characters (e.g. ZERO WIDTH SPACE)
# that are not matched by \s. Strip a small known set before regex matching.
_INVISIBLE = {"\u200b", "\u200c", "\u200d", "\ufeff"}


def _strip_invisible(s: str) -> str:
    if not s:
        return s
    return "".join(ch for ch in s if ch not in _INVISIBLE)


def _split_cn_sentences(text: str) -> list[str]:
    s = (text or "").strip()
    if not s:
        return []
    out: list[str] = []
    buf: list[str] = []
    for ch in s:
        buf.append(ch)
        if ch in {"。", "？", "！", "?", "!"}:
            seg = "".join(buf).strip()
            if seg:
                out.append(seg)
            buf.clear()
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def extract_dialogue_sentences(text: str) -> list[ExtractedSentence]:
    """
    Extract example sentences from a Dialogues: block.

    Heuristics:
    - Enter dialogue mode when a line equals 'Dialogues:' (case-insensitive, full line).
    - Within the block, accept speaker lines like 'A: ...' or 'B：...'.
    - Exit dialogue mode on a new section header-ish line after at least one speaker line,
      or on two consecutive blank lines after at least one speaker line.
    - Segment speaker text by Chinese sentence punctuation (。！？) and also ASCII ?/!.
    """

    lines = (text or "").splitlines()
    in_dialogue = False
    saw_speaker = False
    blank_run = 0
    out: list[ExtractedSentence] = []

    for idx, raw in enumerate(lines):
        line = raw.rstrip("\n")
        stripped = _strip_invisible(line.strip())

        if not in_dialogue:
            m_header = _RE_DIALOGUE_HEADER.match(stripped)
            if m_header:
                in_dialogue = True
                saw_speaker = False
                blank_run = 0
            continue

        # in_dialogue
        if not stripped:
            blank_run += 1
            if saw_speaker and blank_run >= 2:
                in_dialogue = False
                saw_speaker = False
                blank_run = 0
                continue
            continue
        blank_run = 0

        if saw_speaker and _RE_SECTION_HEADERISH.match(stripped) and not _RE_SPEAKER_LINE.match(stripped):
            in_dialogue = False
            saw_speaker = False
            blank_run = 0
            continue

        m = _RE_SPEAKER_LINE.match(_strip_invisible(line))
        if not m:
            # Ignore non-speaker lines inside the block; do not end immediately (PDF artifacts).
            continue

        saw_speaker = True
        speaker = m.group(1).upper()
        content = m.group(2).strip()
        for sent in _split_cn_sentences(content):
            out.append(ExtractedSentence(text=sent, source=f"dialogue:{speaker}:line{idx+1}"))

    return out

