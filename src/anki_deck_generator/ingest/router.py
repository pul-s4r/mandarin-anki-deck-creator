from __future__ import annotations

from pathlib import Path

from anki_deck_generator.ingest.docx import extract_text_from_docx
from anki_deck_generator.ingest.markdown import extract_text_from_markdown_path
from anki_deck_generator.ingest.pdf import extract_text_from_pdf


def extract_text_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(path)
    if suffix in {".md", ".markdown"}:
        return extract_text_from_markdown_path(path)
    if suffix == ".docx":
        return extract_text_from_docx(path)
    raise ValueError(f"Unsupported input type: {suffix} ({path})")
