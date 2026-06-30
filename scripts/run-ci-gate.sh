#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# Use virtual environment if it exists
if [[ -f "$PROJECT_ROOT/.venv/bin/pip" ]]; then
    PIP="$PROJECT_ROOT/.venv/bin/pip"
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
else
    PIP="pip"
    PYTHON="python"
fi

echo "=== CI Gate: Installing dependencies ==="
$PIP install -e ".[dev,google-drive,ankiweb,server]" -q

echo "=== CI Gate: Running pytest ==="
$PYTHON -m pytest -q

echo "=== CI Gate: PASSED ==="
