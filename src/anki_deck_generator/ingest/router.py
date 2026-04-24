from __future__ import annotations

from pathlib import Path

from anki_deck_generator.errors import IngestError
from anki_deck_generator.ingest.docx import extract_text_from_docx_bytes
from anki_deck_generator.ingest.markdown import extract_text_from_markdown_bytes
from anki_deck_generator.ingest.pdf import extract_text_from_pdf_bytes


def extract_text_from_bytes(data: bytes, *, format: str) -> str:
    fmt = (format or "").strip().lower()
    if fmt == "pdf":
        return extract_text_from_pdf_bytes(data)
    if fmt in {"markdown", "md"}:
        return extract_text_from_markdown_bytes(data)
    if fmt == "docx":
        return extract_text_from_docx_bytes(data)
    raise IngestError(f"Unsupported format: {format!r}")


def extract_text_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_bytes(path.read_bytes(), format="pdf")
    if suffix in {".md", ".markdown"}:
        return extract_text_from_bytes(path.read_bytes(), format="markdown")
    if suffix == ".docx":
        return extract_text_from_bytes(path.read_bytes(), format="docx")
    raise IngestError(f"Unsupported input type: {suffix} ({path})")
