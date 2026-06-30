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
SOURCE_SET_CONFIG="${SOURCE_SET_CONFIG:-$HOME/Documents/anki-pipeline-test-state.yaml}"
SOURCE_SET=""
STATE_DB="${STATE_DB:-$HOME/.local/share/anki-notes-pipeline/state.db}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
REPORTS_DIR="${REPORTS_DIR:-$PROJECT_ROOT/reports}"

usage() {
    cat <<EOF
Usage: $0 [options]

Run pipeline with real LLM (Amazon Bedrock). Supports local files and Google Drive.

Mode 1 - Local file:
  --input <file>        Input PDF, Markdown, or DOCX file
  --output <csv>        Output CSV path (default: reports/<timestamp>.csv)

Mode 2 - Source set config (local + Google Drive):
  --source-set <name>   Source set name from YAML config
  --source-set-config <yaml>  Path to YAML config (default: \$SOURCE_SET_CONFIG or ~/Documents/anki-pipeline-test-state.yaml)

Common options:
  --cedict-path <path>  Path to cedict_ts.u8 (required for enrichment)
  --state-db <path>     SQLite state DB path (default: \$STATE_DB or ~/.local/share/anki-notes-pipeline/state.db)
  --reports-dir <path>  Output directory (default: ./reports)

Environment variables:
  SOURCE_SET_CONFIG     Default source set config path
  STATE_DB              Default state DB path
  REPORTS_DIR           Default reports directory

Examples:
  # Local file
  $0 --input ~/Documents/notes.pdf --cedict-path /path/to/cedict_ts.u8

  # Google Drive source set
  $0 --source-set demo-drive --source-set-config ~/Documents/my-sources.yaml --cedict-path /path/to/cedict_ts.u8

  # Custom reports directory
  REPORTS_DIR=/tmp/exports $0 --input notes.pdf --cedict-path /path/to/cedict_ts.u8
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --input) INPUT="$2"; shift 2 ;;
        --output) OUTPUT="$2"; shift 2 ;;
        --cedict-path) CEDICT_PATH="$2"; shift 2 ;;
        --source-set) SOURCE_SET="$2"; shift 2 ;;
        --source-set-config) SOURCE_SET_CONFIG="$2"; shift 2 ;;
        --state-db) STATE_DB="$2"; shift 2 ;;
        --reports-dir) REPORTS_DIR="$2"; shift 2 ;;
        *) usage ;;
    esac
done

if [[ -z "$CEDICT_PATH" ]]; then
    echo "Error: --cedict-path is required" >&2
    exit 1
fi

if [[ ! -f "$CEDICT_PATH" ]]; then
    echo "Error: CEDICT file not found: $CEDICT_PATH" >&2
    exit 1
fi

mkdir -p "$REPORTS_DIR"

if [[ -n "$SOURCE_SET" ]]; then
    # Mode 2: Source set config
    if [[ ! -f "$SOURCE_SET_CONFIG" ]]; then
        echo "Error: Source set config not found: $SOURCE_SET_CONFIG" >&2
        exit 1
    fi

    # Initialize state DB if needed
    if [[ ! -f "$STATE_DB" ]]; then
        echo "Initializing state DB: $STATE_DB"
        mkdir -p "$(dirname "$STATE_DB")"
        $PYTHON -m anki_deck_generator.cli state init --db-path "$STATE_DB"
    fi

    echo "=== Live Run: Source Set Mode ==="
    echo "Source set:    $SOURCE_SET"
    echo "Config:        $SOURCE_SET_CONFIG"
    echo "State DB:      $STATE_DB"
    echo "CEDICT:        $CEDICT_PATH"
    echo "Reports dir:   $REPORTS_DIR"
    echo ""

    $PYTHON -m anki_deck_generator.cli schedule \
        --source-set "$SOURCE_SET" \
        --state-db "$STATE_DB" \
        --source-set-config "$SOURCE_SET_CONFIG" \
        --cedict-path "$CEDICT_PATH"

else
    # Mode 1: Local file
    if [[ -z "$INPUT" ]]; then
        echo "Error: Either --input or --source-set is required" >&2
        usage
    fi

    if [[ ! -f "$INPUT" ]]; then
        echo "Error: Input file not found: $INPUT" >&2
        exit 1
    fi

    if [[ -z "$OUTPUT" ]]; then
        OUTPUT="$REPORTS_DIR/${TIMESTAMP}.csv"
    fi

    echo "=== Live Run: Local File Mode ==="
    echo "Input:     $INPUT"
    echo "Output:    $OUTPUT"
    echo "CEDICT:    $CEDICT_PATH"
    echo ""

    $PYTHON -m anki_deck_generator.cli run \
        "$INPUT" \
        --output "$OUTPUT" \
        --cedict-path "$CEDICT_PATH"

fi

echo ""
echo "=== Live Run: COMPLETE ==="
echo "Output: ${OUTPUT:-<see source set exporters>}"
