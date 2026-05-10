from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextBlock:
    kind: str  # "table" | "text"
    text: str


def segment_table_blocks(text: str) -> list[TextBlock]:
    """
    Split extracted text into table-like blocks (tab/column regions) and normal text blocks.

    Heuristic: consecutive lines where a majority contain literal tab characters and the run is
    at least 3 lines long.
    """

    lines = text.splitlines()
    blocks: list[TextBlock] = []

    def flush(kind: str, buf: list[str]) -> None:
        if not buf:
            return
        blocks.append(TextBlock(kind=kind, text="\n".join(buf).strip("\n")))
        buf.clear()

    i = 0
    text_buf: list[str] = []
    while i < len(lines):
        # Probe a potential run starting at i
        j = i
        run: list[str] = []
        tab_lines = 0
        while j < len(lines):
            ln = lines[j]
            if not ln.strip():
                # allow blank lines inside a run, but count them as non-tab
                run.append(ln)
                j += 1
                continue
            if "\t" in ln:
                tab_lines += 1
                run.append(ln)
                j += 1
                continue
            # If we have started collecting a run and hit a non-tab line, stop probing.
            if run:
                break
            # Otherwise, this is normal text.
            break

        non_empty = [r for r in run if r.strip()]
        is_table_run = len(run) >= 3 and len(non_empty) > 0 and (tab_lines / max(1, len(non_empty))) >= 0.5

        if is_table_run:
            flush("text", text_buf)
            flush("table", run)
            i = j
        else:
            text_buf.append(lines[i])
            i += 1

    flush("text", text_buf)
    return [b for b in blocks if b.text.strip()]

