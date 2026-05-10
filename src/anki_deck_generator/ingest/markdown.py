from __future__ import annotations

from pathlib import Path


def extract_text_from_markdown_bytes(data: bytes) -> str:
    return data.decode("utf-8")


def extract_text_from_markdown_path(path: Path) -> str:
    return extract_text_from_markdown_bytes(path.read_bytes())
