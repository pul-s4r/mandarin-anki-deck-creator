from __future__ import annotations

from pathlib import Path

import pytest

from anki_deck_generator.errors import IngestError
from anki_deck_generator.ingest.router import (
    extract_text_from_bytes,
    extract_text_from_path,
)

_REPO = Path(__file__).resolve().parents[1]
_BASELINE_INPUTS = _REPO / "tests" / "baselines" / "inputs"


def test_extract_text_from_bytes_matches_path_md(tmp_path: Path) -> None:
    p = tmp_path / "n.md"
    p.write_text("你好\n", encoding="utf-8")
    from_path = extract_text_from_path(p)
    from_bytes = extract_text_from_bytes(p.read_bytes(), format="markdown")
    assert from_bytes == from_path


def test_extract_text_from_bytes_unknown_format() -> None:
    with pytest.raises(IngestError, match="Unsupported format"):
        extract_text_from_bytes(b"x", format="unknown")


@pytest.mark.parametrize(
    ("rel", "fmt"),
    [
        ("sample.md", "markdown"),
        ("sample.docx", "docx"),
        ("sample.pdf", "pdf"),
    ],
)
def test_extract_text_from_bytes_matches_path_baseline_inputs(
    rel: str, fmt: str
) -> None:
    p = _BASELINE_INPUTS / rel
    if not p.is_file():
        pytest.skip(f"missing {p}")
    data = p.read_bytes()
    assert extract_text_from_bytes(data, format=fmt) == extract_text_from_path(p)
