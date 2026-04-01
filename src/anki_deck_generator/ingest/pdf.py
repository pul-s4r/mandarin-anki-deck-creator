from __future__ import annotations

from pathlib import Path


def extract_text_from_pdf(path: Path) -> str:
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    try:
        parts: list[str] = []
        for idx, page in enumerate(doc):
            page_text = page.get_text()
            parts.append(page_text)
        out = "\n".join(parts)
        return out
    finally:
        doc.close()
