from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINES = REPO_ROOT / "tests" / "baselines"
INPUTS = BASELINES / "inputs"
OUTPUTS = BASELINES / "outputs"
LLM_MOCK = BASELINES / "llm_mock.json"
CEDICT = BASELINES / "cedict_sample.u8"


def _run_cli_run(out_csv: Path, input_path: Path) -> None:
    env = os.environ.copy()
    env["ANKI_PIPELINE_LLM_FIXTURE_PATH"] = str(LLM_MOCK)
    cmd = [
        sys.executable,
        "-m",
        "anki_deck_generator.cli",
        "run",
        str(input_path),
        "-o",
        str(out_csv),
        "--cedict-path",
        str(CEDICT),
        "--disable-sentences",
        "--no-skip-lines-filter",
    ]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout


@pytest.mark.parametrize(
    "name",
    ["sample.md", "sample.docx", "sample.pdf"],
)
def test_script_mode_baseline_csv_byte_parity(tmp_path: Path, name: str) -> None:
    inp = INPUTS / name
    expected = OUTPUTS / f"{name}.csv"
    assert inp.is_file(), inp
    assert expected.is_file(), expected
    out = tmp_path / "out.csv"
    _run_cli_run(out, inp)
    assert out.read_bytes() == expected.read_bytes()


def test_bare_venv_install_and_cli_help(tmp_path: Path) -> None:
    vdir = tmp_path / "barevenv"
    subprocess.run([sys.executable, "-m", "venv", str(vdir)], check=True)
    py = vdir / "bin" / "python"
    pip = vdir / "bin" / "pip"
    subprocess.run([str(pip), "install", "-q", "."], cwd=REPO_ROOT, check=True)
    subprocess.run([str(py), "-m", "anki_deck_generator.cli", "--help"], cwd=REPO_ROOT, check=True)
    subprocess.run([str(py), "-m", "anki_deck_generator.cli", "run", "--help"], cwd=REPO_ROOT, check=True)


def test_package_import_does_not_resolve_blocked_optional_deps() -> None:
    code = r"""
import importlib.abc
import sys

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ImportError("blocked", name)
        if name == "openpyxl" or name.startswith("openpyxl."):
            raise ImportError("blocked", name)
        if name == "googleapiclient" or name.startswith("googleapiclient."):
            raise ImportError("blocked", name)
        if name == "yaml" or name.startswith("yaml."):
            raise ImportError("blocked", name)
        return None

sys.meta_path.insert(0, Blocker())
import anki_deck_generator  # noqa: F401
print("ok")
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_cli_parse_run_does_not_import_state_sync_or_yaml(tmp_path: Path) -> None:
    """
    Lazy-loading boundary: importing CLI + parsing ``run`` args must not load state/sync stacks or PyYAML.

    Run in a subprocess so ``sys.modules`` is clean (pytest may have imported them already).
    """
    probe_out = tmp_path / "out.csv"
    code = f"""
import sys
from pathlib import Path

REPO_ROOT = Path({str(REPO_ROOT)!r})
PROBE_OUT = Path({str(probe_out)!r})

import importlib.abc

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "yaml" or name.startswith("yaml."):
            raise ImportError("blocked", name)
        return None

sys.meta_path.insert(0, Blocker())

import anki_deck_generator.cli as cli_mod

p = cli_mod._build_parser()
p.parse_args(
    [
        "run",
        str(REPO_ROOT / "tests" / "baselines" / "inputs" / "sample.md"),
        "-o",
        str(PROBE_OUT),
        "--cedict-path",
        str(REPO_ROOT / "tests" / "baselines" / "cedict_sample.u8"),
        "--disable-sentences",
        "--no-skip-lines-filter",
    ]
)

for name in ("yaml", "anki_deck_generator.state", "anki_deck_generator.sync"):
    if name in sys.modules:
        raise SystemExit(f"unexpectedly loaded: {{name}}")
print("ok")
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
