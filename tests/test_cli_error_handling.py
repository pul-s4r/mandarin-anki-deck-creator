from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(
    args: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    base = os.environ.copy()
    if env:
        base.update(env)
    return subprocess.run(
        [sys.executable, "-m", "anki_deck_generator.cli", *args],
        cwd=REPO_ROOT,
        env=base,
        capture_output=True,
        text=True,
    )


def test_cli_unsupported_input_suffix(tmp_path: Path) -> None:
    bad = tmp_path / "notes.xyz"
    bad.write_bytes(b"x")
    out = tmp_path / "out.csv"
    proc = _run_cli(["run", str(bad), "-o", str(out)])
    assert proc.returncode == 1
    assert "error:" in proc.stderr
    assert "Unsupported input type" in proc.stderr


def test_cli_llm_fixture_missing_chunk(tmp_path: Path) -> None:
    mock = tmp_path / "empty.json"
    mock.write_text(json.dumps({"chunks": {}, "translations": {}}), encoding="utf-8")
    inp = REPO_ROOT / "tests" / "baselines" / "inputs" / "sample.md"
    out = tmp_path / "out.csv"
    cedict = REPO_ROOT / "tests" / "baselines" / "cedict_sample.u8"
    proc = _run_cli(
        [
            "run",
            str(inp),
            "-o",
            str(out),
            "--cedict-path",
            str(cedict),
            "--disable-sentences",
            "--no-skip-lines-filter",
        ],
        env={"ANKI_PIPELINE_LLM_FIXTURE_PATH": str(mock)},
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr
    assert "LLM fixture missing chunk key" in proc.stderr
