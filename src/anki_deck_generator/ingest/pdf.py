from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF


def extract_text_from_pdf_bytes(data: bytes) -> str:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        parts: list[str] = []
        for page in doc:
            page_text = page.get_text()
            parts.append(page_text)
        return "\n".join(parts)
    finally:
        doc.close()


def extract_text_from_pdf(path: Path) -> str:
    return extract_text_from_pdf_bytes(path.read_bytes())
