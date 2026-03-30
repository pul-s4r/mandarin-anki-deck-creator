from __future__ import annotations

from pathlib import Path


def extract_text_from_docx(path: Path) -> str:
    from docx import Document

    document = Document(path)
    blocks: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            blocks.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append("\t".join(cells))
    return "\n".join(blocks)
