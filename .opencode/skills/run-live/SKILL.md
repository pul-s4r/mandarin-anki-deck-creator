# run-live

Run the pipeline with real LLM (Amazon Bedrock). Supports local files and Google Drive.

## Trigger Phrases

- "live run"
- "sync notes"
- "run live"
- "process notes"
- "extract vocabulary"

## Usage

### Mode 1: Local File

```bash
./scripts/run-live.sh --input <file> --cedict-path <path> [options]
```

### Mode 2: Source Set Config (local + Google Drive)

```bash
./scripts/run-live.sh --source-set <name> --cedict-path <path> [options]
```

### Required Arguments

| Argument | Description |
|----------|-------------|
| `--input <file>` | Input PDF, Markdown, or DOCX file (Mode 1) |
| `--source-set <name>` | Source set name from YAML config (Mode 2) |
| `--cedict-path <path>` | Path to cedict_ts.u8 |

### Optional Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--output <csv>` | Output CSV path (Mode 1) | `reports/<timestamp>.csv` |
| `--source-set-config <yaml>` | Path to YAML config | `$SOURCE_SET_CONFIG` or `~/Documents/anki-pipeline-test-state.yaml` |
| `--state-db <path>` | SQLite state DB path | `$STATE_DB` or `~/.local/share/anki-notes-pipeline/state.db` |
| `--reports-dir <path>` | Output directory | `./reports` |

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SOURCE_SET_CONFIG` | Default source set config path | `~/Documents/anki-pipeline-test-state.yaml` |
| `STATE_DB` | Default state DB path | `~/.local/share/anki-notes-pipeline/state.db` |
| `REPORTS_DIR` | Default reports directory | `./reports` |

## Examples

```bash
# Local file
./scripts/run-live.sh --input ~/Documents/notes.pdf --cedict-path /path/to/cedict_ts.u8

# Google Drive source set
./scripts/run-live.sh --source-set demo-drive --source-set-config ~/Documents/my-sources.yaml --cedict-path /path/to/cedict_ts.u8

# Custom reports directory
REPORTS_DIR=/tmp/exports ./scripts/run-live.sh --input notes.pdf --cedict-path /path/to/cedict_ts.u8
```

## What It Does

1. **Mode 1 (Local file)**: Runs `anki-notes-pipeline run` on a single file
2. **Mode 2 (Source set)**: Runs `anki-notes-pipeline schedule` with state tracking
   - Processes local files and/or Google Drive sources
   - Tracks document and chunk hashes for incremental sync
   - Skips unchanged content
   - Exports to configured destinations (CSV, XLSX)

## Prerequisites

- `pip install -e ".[sync,google-drive]"` (for Google Drive support)
- AWS credentials configured (for Bedrock LLM calls)
- CEDICT file (`cedict_ts.u8`)
- Google Drive OAuth token (for Drive sources): `anki-notes-pipeline auth google-drive --client-secrets <path>`

## Source Set Config

See `scripts/sources.yaml.example` for the full schema.

Example YAML:

```yaml
schema_version: 1
source_sets:
  my-notes:
    sources:
      - provider: local-filesystem
        path: ~/Documents/notes.pdf
      - provider: google-drive
        folder_ids: ["YOUR_FOLDER_ID"]
        credentials_file: ~/.config/anki-notes-pipeline/google-drive-token.json
    exporters:
      - type: csv
        destination: ~/Documents/export.csv
```

## Notes

- Creates `reports/` directory if it doesn't exist
- Initializes state DB if it doesn't exist (Mode 2)
- Uses your personal config at `~/Documents/anki-pipeline-test-state.yaml` by default
- Template config: `scripts/sources.yaml.example`
