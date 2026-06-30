#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# Use virtual environment if it exists
if [[ -f "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
else
    PYTHON="python"
fi

INPUT=""
OUTPUT=""
CEDICT_PATH=""
LLM_FIXTURE_PATH="$PROJECT_ROOT/tests/baselines/llm_mock.json"

usage() {
    cat <<EOF
Usage: $0 --input <file> --output <csv> [options]

Run pipeline with mocked LLM fixture (deterministic, no AWS calls).

Required:
  --input <file>        Input PDF, Markdown, or DOCX file
  --output <csv>        Output CSV path

Optional:
  --cedict-path <path>  Path to cedict_ts.u8
  --fixture-path <path> Path to LLM fixture JSON (default: tests/baselines/llm_mock.json)

Examples:
  $0 --input tests/baselines/inputs/sample.pdf --output /tmp/out.csv
  $0 --input notes.md --output out.csv --cedict-path /path/to/cedict_ts.u8
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --input) INPUT="$2"; shift 2 ;;
        --output) OUTPUT="$2"; shift 2 ;;
        --cedict-path) CEDICT_PATH="$2"; shift 2 ;;
        --fixture-path) LLM_FIXTURE_PATH="$2"; shift 2 ;;
        *) usage ;;
    esac
done

if [[ -z "$INPUT" || -z "$OUTPUT" ]]; then
    usage
fi

if [[ ! -f "$INPUT" ]]; then
    echo "Error: Input file not found: $INPUT" >&2
    exit 1
fi

if [[ ! -f "$LLM_FIXTURE_PATH" ]]; then
    echo "Error: LLM fixture not found: $LLM_FIXTURE_PATH" >&2
    exit 1
fi

CMD="$PYTHON -m anki_deck_generator.cli run \"$INPUT\" --output \"$OUTPUT\" --llm-fixture-path \"$LLM_FIXTURE_PATH\" --disable-sentences --no-skip-lines-filter"

if [[ -n "$CEDICT_PATH" ]]; then
    CMD="$CMD --cedict-path \"$CEDICT_PATH\""
fi

echo "=== Mocked Run ==="
echo "Input:   $INPUT"
echo "Output:  $OUTPUT"
echo "Fixture: $LLM_FIXTURE_PATH"
echo "CEDICT:  ${CEDICT_PATH:-<none>}"
echo ""
echo "Running: $CMD"
echo ""

eval "$CMD"

echo ""
echo "=== Mocked Run: COMPLETE ==="
echo "Output written to: $OUTPUT"
