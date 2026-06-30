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

SOURCE_SET_CONFIG="${SOURCE_SET_CONFIG:-$HOME/Documents/anki-pipeline-test-state.yaml}"
SOURCE_SET=""
STATE_DB="${STATE_DB:-$HOME/.local/share/anki-notes-pipeline/state.db}"
CEDICT_PATH=""
ANKI_DECK_NAME="${ANKI_DECK_NAME:-}"
ANKI_MODEL_NAME="Chinese vocabulary"
ANKI_CONNECT_URL="http://127.0.0.1:8765"

usage() {
    cat <<EOF
Usage: $0 --source-set <name> [options]

Push cards to desktop Anki via AnkiConnect.
Requires Anki to be running with AnkiConnect plugin installed.

Required:
  --source-set <name>   Source set name from YAML config
  --cedict-path <path>  Path to cedict_ts.u8

Optional:
  --source-set-config <yaml>  Path to YAML config (default: \$SOURCE_SET_CONFIG or ~/Documents/anki-pipeline-test-state.yaml)
  --state-db <path>     SQLite state DB path (default: \$STATE_DB or ~/.local/share/anki-notes-pipeline/state.db)
  --anki-deck-name <name>   Target Anki deck (overrides YAML config)
  --anki-model-name <name>  Anki note type (default: Chinese vocabulary)
  --anki-connect-url <url>  AnkiConnect URL (default: http://127.0.0.1:8765)

Environment variables:
  SOURCE_SET_CONFIG     Default source set config path
  STATE_DB              Default state DB path

Examples:
  # Push to default deck
  $0 --source-set demo-anki --cedict-path /path/to/cedict_ts.u8

  # Push to specific deck
  $0 --source-set demo --cedict-path /path/to/cedict_ts.u8 --anki-deck-name "Chinese::301"

  # Custom config and state DB
  $0 --source-set my-notes \\
      --source-set-config ~/Documents/my-sources.yaml \\
      --state-db ~/Documents/my-state.db \\
      --cedict-path /path/to/cedict_ts.u8

Prerequisites:
  1. Install Anki with AnkiConnect plugin
  2. Start Anki (AnkiConnect listens on port 8765 by default)
  3. Ensure source set YAML has 'type: anki' exporter OR use --anki-deck-name
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --source-set) SOURCE_SET="$2"; shift 2 ;;
        --source-set-config) SOURCE_SET_CONFIG="$2"; shift 2 ;;
        --state-db) STATE_DB="$2"; shift 2 ;;
        --cedict-path) CEDICT_PATH="$2"; shift 2 ;;
        --anki-deck-name) ANKI_DECK_NAME="$2"; shift 2 ;;
        --anki-model-name) ANKI_MODEL_NAME="$2"; shift 2 ;;
        --anki-connect-url) ANKI_CONNECT_URL="$2"; shift 2 ;;
        *) usage ;;
    esac
done

if [[ -z "$SOURCE_SET" ]]; then
    echo "Error: --source-set is required" >&2
    usage
fi

if [[ -z "$CEDICT_PATH" ]]; then
    echo "Error: --cedict-path is required" >&2
    exit 1
fi

if [[ ! -f "$SOURCE_SET_CONFIG" ]]; then
    echo "Error: Source set config not found: $SOURCE_SET_CONFIG" >&2
    exit 1
fi

if [[ ! -f "$CEDICT_PATH" ]]; then
    echo "Error: CEDICT file not found: $CEDICT_PATH" >&2
    exit 1
fi

# Initialize state DB if needed
if [[ ! -f "$STATE_DB" ]]; then
    echo "Initializing state DB: $STATE_DB"
    mkdir -p "$(dirname "$STATE_DB")"
    $PYTHON -m anki_deck_generator.cli state init --db-path "$STATE_DB"
fi

echo "=== Anki Upload ==="
echo "Source set:       $SOURCE_SET"
echo "Config:           $SOURCE_SET_CONFIG"
echo "State DB:         $STATE_DB"
echo "CEDICT:           $CEDICT_PATH"
echo "AnkiConnect URL:  $ANKI_CONNECT_URL"
echo "Deck name:        ${ANKI_DECK_NAME:-<from YAML>}"
echo "Model name:       $ANKI_MODEL_NAME"
echo ""

# Check AnkiConnect
echo "Checking AnkiConnect..."
if ! $PYTHON -m anki_deck_generator.export.ankiweb.anki_connect --url "$ANKI_CONNECT_URL" 2>/dev/null; then
    echo "Warning: AnkiConnect not responding at $ANKI_CONNECT_URL" >&2
    echo "Make sure Anki is running with AnkiConnect plugin installed." >&2
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi
echo ""

CMD="$PYTHON -m anki_deck_generator.cli schedule \\
    --source-set \"$SOURCE_SET\" \\
    --state-db \"$STATE_DB\" \\
    --source-set-config \"$SOURCE_SET_CONFIG\" \\
    --cedict-path \"$CEDICT_PATH\" \\
    --to-anki \\
    --anki-model-name \"$ANKI_MODEL_NAME\" \\
    --anki-connect-url \"$ANKI_CONNECT_URL\""

if [[ -n "$ANKI_DECK_NAME" ]]; then
    CMD="$CMD --anki-deck-name \"$ANKI_DECK_NAME\""
fi

echo "Running: $CMD"
echo ""

eval "$CMD"

echo ""
echo "=== Anki Upload: COMPLETE ==="
